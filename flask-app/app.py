# app.py
import datetime
import os
import boto3
from botocore.exceptions import ClientError

from flask import Flask, jsonify, request

app = Flask(__name__)

USERS_TABLE = os.environ.get('USERS_TABLE') or "users-table-dev"
client = boto3.client("dynamodb")
iam_client = boto3.client("iam")


@app.route("/")
def hello():
    return "Hello World! ".format(datetime.datetime.now())


@app.route("/sync-users")
def sync_db():
    result = jsonify(perform_db_sync())
    return result, 200


@app.route("/get-user/<string:user_name>")
def scan_db(user_name):
    try:
        attrs_to_get = ["userId", "UserName", "CreateDate"]
        response = client.scan(TableName=USERS_TABLE, AttributesToGet=attrs_to_get,
                               Select="SPECIFIC_ATTRIBUTES",
                               ScanFilter={
                                   "UserName": {'AttributeValueList': [{'S': '{0}'.format(user_name)}],
                                                'ComparisonOperator': 'EQ'}})
        item = response.get("Items")
        if item:
            result = jsonify(item)
            return result, 200
        else:
            return jsonify({"error": "User not found"}), 404
    except ClientError:
        return jsonify({"error": "User not found"}), 404


@app.route("/list-iam-users")
def list_iam_users():
    iam_users = list_users_from_db()
    return jsonify(iam_users), 200


@app.route("/create-iam-user", methods=["POST"])
def create_iam_user():
    results = []
    name = request.get_json(force=True).get('name')
    if not name:
        return jsonify({'error': 'Please provide name'}), 400

    try:
        response = iam_client.create_user(UserName=name)
        user_detail = response.get("User")
        results.append(user_detail)
        sync_result = perform_db_sync()
        results.append(sync_result)
    except Exception as E:  # botocore.errorfactory.EntityAlreadyExistsException:
        results.append({'error': f'{E}'})

    return jsonify(results), 400


@app.route("/delete-iam-user", methods=["POST"])
def delete_iam_user():
    results = []
    name = request.get_json(force=True).get('name')
    print("User to be deleted in new function {0}".format(name))
    if not name:
        return jsonify({'error': 'Please provide name'}), 400

    try:
        db_id = delete_user_from_db(name)
        print("Id to be deleted is {0}".format(db_id))
        db_delete_response = client.delete_item(TableName=USERS_TABLE, Key={"userId": {"S": db_id}})
        db_delete_status = get_response_status(db_delete_response)
        status_collector = dict()
        if db_delete_status == 200:
            response = iam_client.delete_user(UserName=name)
            status = get_response_status(response)
            if status == 200:
                status_collector.update({"DynamoDBStatus": "Dynamodb in sync with IAM user list"})
                status_collector.update({"IAMStatus": "user deleted from IAM"})
            else:
                status_collector.update({"DynamoDBStatus": "Dynamodb not in sync with IAM user list"})
                status_collector.update({"IAMStatus": "User not deleted from IAM"})
        else:
            status_collector.update({"DynamoDBStatus": "Dynamodb not in sync with IAM user list"})
            status_collector.update({"IAMStatus": "User not deleted from IAM"})

        sync_result = perform_db_sync()
        results.append(status_collector)
        results.append(sync_result)
    except ClientError as E:  # botocore.errorfactory.EntityAlreadyExistsException:
        results.append({'error': f'{E}'})

    return jsonify(results), 400


def delete_user_from_db(delete_user):
    _id = ""
    available_users = get_users_from_iam()
    for each_user in available_users:
        if each_user.get("UserName") == delete_user:
            _id = each_user.get("UserId")
            print("id found in function {0}".format(_id))
            break
    return _id


def get_response_status(response):
    return response.get("ResponseMetadata").get("HTTPStatusCode")


def get_users_from_iam():
    user_list = []
    paginator = iam_client.get_paginator("list_users")
    for response in paginator.paginate():
        for each_user in response["Users"]:
            user_list.append(each_user)
    return user_list


def modify_user_details(user_data):
    modified_user_data = dict()
    for k, v in user_data.items():
        if k == "UserId":
            k = "userId"
        if isinstance(v, datetime.datetime):
            v = v.isoformat()
        modified_user_data[k] = {"S": v}
    return modified_user_data


def perform_db_sync():
    available_users = get_users_from_iam()
    print(available_users)
    result = {"SuccessfulSync": 0, "UnSuccessfulSync": 0}
    for each_user in available_users:
        each_user = modify_user_details(each_user)
        resp = client.put_item(TableName=USERS_TABLE, Item=each_user)
        status = get_response_status(resp)
        if status == 200:
            result["SuccessfulSync"] += 1
        else:
            result["UnSuccessfulSync"] += 1
    return result


def list_users_from_db():
    attrs_to_get = ["userId", "UserName", "CreateDate"]
    available_users = dict()
    for attr in attrs_to_get:
        available_users[attr] = []
    response = client.scan(TableName=USERS_TABLE, AttributesToGet=attrs_to_get,
                           Select="SPECIFIC_ATTRIBUTES", ConsistentRead=True).get("Items")
    for i in response:
        for attr in attrs_to_get:
            available_users.get(attr).extend([i.get(attr).get("S")])

    return available_users
