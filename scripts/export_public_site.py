"""Export the public projection for review/first deployment, without mutation."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_v3.store import Store
from agent_v3.public_site import snapshot

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('output');args=parser.parse_args()
    store=Store()
    try:data=snapshot(store)
    finally:store.close()
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    print(json.dumps({'path':str(target),'bytes':target.stat().st_size,'generated_at':data['generated_at'],
        'counts':data['overview']['counts'],'creatives':len(data['overview']['creatives'])},ensure_ascii=False))
