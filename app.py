# Importing flask module in the project is mandatory
# An object of Flask class is our WSGI application.
from flask import Flask, request, jsonify

import sys
from metaflow import Flow
from random import choice
import json
from pymongo import MongoClient


cluster = MongoClient("mongodb+srv://notshashwat:ZvO4miPdln1uSKsg@cluster0.ii27b7a.mongodb.net/food-delivery?retryWrites=true&w=majority")

db = cluster["food-delivery"]
orders = db["orders"]

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

      
# Flask constructor takes the name of 
# current module (__name__) as argument.
app = Flask(__name__)

# The route() function of the Flask class is a decorator, 
# which tells the application which URL should call 
# the associated function.
# ‘/’ URL is bound with hello_world() function.
@app.route('/recommend', methods=["POST"])
def recommend():
	print(request)
	items = None
	try : 
		userId = request.args.get("userId")
		# print("userid", userId)
		temp = orders.find({"userID" : userId}).sort({"_id":-1})
		# print(temp[0])
		restId = temp[0]["restID"]
		item_name = temp[0]["items"][0]["orderid"]
		concat_str = restId + "|||" + item_name
		# print(concat_str)
		recs = get_recs( concat_str, 10)
		# json_recs = json.dumps(recs)
		items = json.dumps([rec.split("|||")[0] for rec in recs])
		
	except Exception as e: 
		print(e)
	return items
            

	# query_item, n = sys.argv[1], int(sys.argv[2])
	# return json_recs

# main driver function
if __name__ == '__main__':

	# run() method of Flask class runs the application 
	# on the local development server.
	app.run()

