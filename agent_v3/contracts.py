"""Provider-neutral role, evidence and structured-delivery contracts."""
import copy
import json
import re
import time
from dataclasses import dataclass

from jsonschema import Draft202012Validator
from agent_v2.store import dump, stable_id
from .opencode_zen import StructuredDeliveryError

VERSION = 'agent-contract-v3.17'
ROLES = {'readiness':'system', 'main_plan':'business_main', 'intelligence':'business_main',
         'creative_plan':'business_main','creative_production':'business_main','creative':'business_main',
         'research_plan':'research_child','interpretation':'research_child','event_relation':'research_child','community_summary':'research_child'}
SAFE_METADATA = ('model','api_model','usage','seconds','transport','session_id','request_id',
    'reported_cost','cost_status','reasoning_effort','output_limit','finish_reason','reasoning_present','input_file','task_id',
    'provider_id','profile_version','schema_errors')


def safe_schema_errors(items):
    rules={'additionalProperties','maxItems','minItems','type','enum','required','pattern',
           'maxLength','minLength','maximum','minimum','uniqueItems','const','oneOf','anyOf'}
    if not isinstance(items,list):return []
    return [{'path':item['path'],'constraint':item['constraint']} for item in items[:5]
            if isinstance(item,dict) and isinstance(item.get('path'),str) and
            re.fullmatch(r'[A-Za-z0-9_/-]{1,200}',item['path']) and item.get('constraint') in rules]


@dataclass(frozen=True)
class AgentContract:
    stage: str
    schema: dict
    input_fingerprint: str
    version: str = VERSION

    @property
    def role(self):
        return ROLES[self.stage]

    @classmethod
    def create(cls, stage, packet, schema):
        if stage not in ROLES:
            raise ValueError('未注册的 Agent 阶段')
        Draft202012Validator.check_schema(schema)
        if not isinstance(packet,dict):
            raise ValueError('Agent 输入必须是对象')
        return cls(stage,copy.deepcopy(schema),stable_id('input_',dump(packet)))

    def metadata(self):
        return {'contract_version':self.version,'stage':self.stage,'agent_role':self.role,
                'input_fingerprint':self.input_fingerprint}

    def validate(self, value, packet):
        Draft202012Validator(self.schema).validate(value)
        if not isinstance(value,dict):
            raise ValueError('Agent 交付必须是 JSON 对象')
        # Never trust a provider that ignored enum/min/max in its output mode.
        refs=packet.get('quote_candidates')
        def walk(node):
            if isinstance(node,dict):
                for key,item in node.items():
                    if refs is not None and key in ('basis_refs','fact_refs'):
                        if not isinstance(item,list) or any(type(i) is not int or not 0<=i<len(refs) for i in item):
                            raise ValueError('交付引用不存在于本次证据包')
                    walk(item)
            elif isinstance(node,list):
                for item in node:walk(item)
        walk(value)
        return value


def chat_task(client,stage,packet,schema,system,*,timeout_seconds=180):
    """Adapt existing chat clients to the same final-JSON task contract."""
    response=client.decide([{'role':'system','content':system+'\n仅输出符合 JSON Schema 的最终 JSON 对象，不调用工具。'},
        {'role':'user','content':dump({'input':packet,'output_schema':schema})}],[])
    metadata={k:response[k] for k in SAFE_METADATA if k in response}
    metadata.setdefault('transport','chat-completions')
    metadata.setdefault('session_id',None)
    metadata.setdefault('usage',{})
    try:
        if response.get('tool_calls') or response.get('finish_reason') in ('length','tool_calls','content_filter'):
            raise ValueError('JSON 任务未完成最终交付')
        metadata['result']=json.loads(response.get('content') or '')
    except (ValueError,TypeError) as error:
        raise StructuredDeliveryError('最终 content 未形成完整 JSON 对象',{**metadata,'result':None}) from error
    return metadata


def run_task(model,stage,packet,schema,system,*,timeout_seconds=180):
    contract=AgentContract.create(stage,packet,schema)
    started=time.monotonic()
    delivery_error=None
    try:
        if callable(getattr(model,'run_task',None)):
            response=model.run_task(stage,copy.deepcopy(packet),copy.deepcopy(schema),system,timeout_seconds=timeout_seconds)
        else:
            response=chat_task(model,stage,packet,schema,system,timeout_seconds=timeout_seconds)
    except StructuredDeliveryError as error:
        response=error.response
        delivery_error=error
    if not isinstance(response,dict):
        raise StructuredDeliveryError('模型适配器未返回交付对象',{'result':None,**contract.metadata()})
    output={k:copy.deepcopy(response[k]) for k in SAFE_METADATA if k in response}
    if 'schema_errors' in output:output['schema_errors']=safe_schema_errors(output['schema_errors'])
    output.update(contract.metadata())
    output.setdefault('model',getattr(model,'model','unknown'))
    output.setdefault('transport',getattr(model,'transport','unknown'))
    output.setdefault('usage',{})
    output.setdefault('seconds',round(time.monotonic()-started,3))
    output.setdefault('session_id',None)
    output['result']=copy.deepcopy(response.get('result'))
    try:
        if delivery_error:raise delivery_error
        if time.monotonic()-started>timeout_seconds:
            raise ValueError('模型交付超过任务时间预算')
        if output.get('finish_reason') in ('length','tool_calls','content_filter'):
            raise ValueError('模型最终交付未正常结束')
        contract.validate(output['result'],packet)
    except Exception as error:
        # Report only paths/constraint names, never raw source or reasoning values.
        path='/'.join(map(str,getattr(error,'absolute_path',()))) or 'root'
        validator=getattr(error,'validator',None) or type(error).__name__
        raise StructuredDeliveryError('Agent 契约未通过：'+path+' / '+validator,output) from error
    return output
