"""Public publication must preserve deliverables without exposing runtime data."""
import json,unittest
from unittest.mock import patch
from agent_v2.store import dump
from agent_v3 import public_site as site
from agent_v3.tests import test_main_agent as fixtures

class PublicSiteTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.MainAgentTests();self.fixture.setUp();self.store=self.fixture.store
    def tearDown(self):self.fixture.tearDown()

    def test_projection_excludes_private_fields_and_local_paths(self):
        value=site.clean({'title':'safe','nested':{'user_id':'private-id','api_key':'secret'},
            'reasoning_content':'private-thought','source_path':'C:/Users/test/private','body':'full source'})
        self.assertEqual(value,{'title':'safe','nested':{}})
        self.assertNotIn('C:/Users',site.clean('stored in C:/Users/test/private'))

    def test_source_exports_links_without_auth_query_or_original_body(self):
        result=site.source({'evidence_id':'e','url':'https://example.com/post?id=1&token=private',
            'body':'full private source','user_name':'person'})
        self.assertEqual(result['url'],'https://example.com/post?id=1')
        self.assertNotIn('body',result);self.assertNotIn('user_name',result)
        self.assertNotIn('url',site.source({'url':'javascript:alert(1)'}))
        self.assertNotIn('url',site.source({'url':'https://secret@example.com/'}))

    def test_material_keeps_editable_delivery_but_not_original_body(self):
        m={'kind':'source_quote','origin':'source','content':'x'*400,
            'source_versions':{'e':{'body':'full post','content_hash':'hash'}},
            'application':{'delivery':{'body':'Editable original copy','audience':'players'}}}
        result=site.material(m)
        self.assertEqual(result['application']['delivery']['body'],'Editable original copy')
        self.assertEqual(len(result['content']),180)
        self.assertEqual(result['source_versions']['e'],{})

    def test_snapshot_is_real_read_only_and_has_no_operator_data(self):
        before=self.store.conn.total_changes
        data=site.snapshot(self.store)
        self.assertEqual(data['schema'],'v3-public-results-1')
        self.assertEqual(self.store.conn.total_changes,before)
        self.assertNotIn('model_options',data['overview'])
        self.assertNotIn('context',data['overview'])
        self.assertNotIn('cycles',data['overview'])
        self.assertFalse(self.store.conn.in_transaction)

    def test_public_verification_quotes_are_bounded(self):
        value=site.signal({'payload':{'facts':[{'quote':'a'*500,'evidence_id':'e'}]}})
        self.assertEqual(len(value['payload']['facts'][0]['quote']),120)

    def test_publisher_uses_data_branch_and_skips_unchanged(self):
        data={'generated_at':'2026-10-08T12:00:00+00:00','overview':{'publication':{'generated_at':'2026-10-08T12:00:00+00:00'}},'topics':{},'events':{}}
        config={'repo':'owner/repo','branch':'site-data'}
        with patch.object(site,'snapshot',return_value=data),patch.object(site,'gh',side_effect=[{'sha':'old'},{'commit':{'sha':'new'}}]) as mock:
            with patch.object(site,'now_iso',return_value=data['generated_at']):
                result=site.publish(self.store,config)
            self.assertEqual(result['commit'],'new')
            self.assertEqual(mock.call_args.kwargs['body']['branch'],'site-data')
            with patch.object(site,'datetime') as clock:
                from datetime import datetime,timezone
                clock.now.return_value=datetime(2026,10,8,12,1,tzinfo=timezone.utc)
                self.assertEqual(site.publish(self.store,config)['status'],'unchanged')
            self.assertEqual(mock.call_count,2)

    def test_publisher_rejects_wrong_branch_before_external_write(self):
        with patch.object(site,'gh') as mock:
            with self.assertRaises(ValueError):site.publish(self.store,{'repo':'owner/repo','branch':'main'})
            mock.assert_not_called()

    def test_github_failure_does_not_persist_command_output(self):
        with patch.object(site.subprocess,'run') as run:
            run.return_value.returncode=1;run.return_value.stderr='secret';run.return_value.stdout='private token'
            with self.assertRaises(RuntimeError) as error:site.gh('api','example')
            self.assertNotIn('secret',str(error.exception));self.assertNotIn('private',str(error.exception))
