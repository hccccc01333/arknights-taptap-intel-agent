"""Offline cross-domain recall. Similarity proposes a relationship; it never merges."""
import json,math,re,threading
from agent_v2.store import stable_id,dump

MODEL='BAAI/bge-small-zh-v1.5'
VERSION='bge-zh-v1.5:cls:256:v1'
_lock=threading.Lock();_encoder=None;_failure=None


def tokens(text):
    text=re.sub(r'\s+','',text.casefold())
    return {text[i:i+2] for i in range(max(0,len(text)-1))}


def encode(store,texts):
    global _encoder,_failure
    vectors=[None]*len(texts);missing=[]
    keys=[stable_id('embedding_',text) for text in texts]
    for i,key in enumerate(keys):
        row=store.conn.execute('SELECT vector FROM semantic_embedding WHERE content_hash=? AND model_version=?',(key,VERSION)).fetchone()
        if row:
            vector=json.loads(row[0])
            if len(vector)==512 and all(isinstance(x,(float,int)) and math.isfinite(x) for x in vector):vectors[i]=vector
        if vectors[i] is None:missing.append(i)
    if not missing:return vectors,{'method':'offline_bge','cached':len(texts),'available':True}
    with _lock:
        try:
            if _failure:raise RuntimeError(_failure)
            if _encoder is None:
                import torch
                from transformers import AutoTokenizer,AutoModel
                torch.set_num_threads(2)
                tok=AutoTokenizer.from_pretrained(MODEL,local_files_only=True)
                model=AutoModel.from_pretrained(MODEL,local_files_only=True);model.eval()
                _encoder=(torch,tok,model)
            torch,tok,model=_encoder
            with torch.no_grad():
                for start in range(0,len(missing),24):
                    ids=missing[start:start+24]
                    packet=tok([texts[i] for i in ids],padding=True,truncation=True,max_length=256,return_tensors='pt')
                    values=torch.nn.functional.normalize(model(**packet).last_hidden_state[:,0],dim=-1).tolist()
                    for i,vector in zip(ids,values):
                        vectors[i]=vector
                        store.conn.execute('INSERT OR IGNORE INTO semantic_embedding VALUES(?,?,?)',(keys[i],VERSION,dump(vector)))
            store.conn.commit()
            return vectors,{'method':'offline_bge','cached':len(texts)-len(missing),'encoded':len(missing),'available':True}
        except Exception as error:
            _failure=type(error).__name__
            return None,{'method':'character_overlap','available':False,'error':_failure}


def pairs(store,documents,*,threshold=.78,max_pairs=200):
    texts=[(d['title']+'\n'+d.get('body','')[:350]) for d in documents]
    vectors,state=encode(store,texts)
    grams=[tokens(d['title']) for d in documents]
    found=[]
    if vectors:
        import numpy as np
        scores=np.asarray(vectors,dtype=np.float32)@np.asarray(vectors,dtype=np.float32).T
    for i in range(len(documents)):
        candidates=[]
        for j in range(i+1,len(documents)):
            score=max(-1.0,min(1.0,float(scores[i,j]))) if vectors else len(grams[i]&grams[j])/max(1,len(grams[i]|grams[j]))
            if score >= (threshold if vectors else .30):candidates.append((score,j))
        for score,j in sorted(candidates,reverse=True)[:3]:found.append((score,i,j))
    found.sort(reverse=True)
    return found[:max_pairs],state
