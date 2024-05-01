import sys




from metaflow import Flow
from random import choice
import json


FLOW_NAME = "DataFlow"
def get_latest_successful_run(flow_name: str):
    "Gets the latest successful run."
    for r in Flow(flow_name).runs():
        if r.successful: 
            return r
        


def get_recs(query_item, n):
    latest_run = get_latest_successful_run(FLOW_NAME)
    latest_model = latest_run.data.final_vectors
    if query_item not in latest_model:
            query_item = choice(list(latest_model.index_to_key))

    recs = [rec[0] for rec in latest_model.most_similar(query_item, topn=n)]
    return recs

      

if __name__ == '__main__':
    query_item, n = sys.argv[1], int(sys.argv[2])
    recs = get_recs( query_item, n)
    json_recs = json.dumps(recs)
    print(json_recs)
    sys.stdout.flush()      
