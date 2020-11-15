from flask import Flask, request
from authlib.integrations.flask_client import OAuth
from splitwise import Splitwise
from splitwise.expense import Expense, ExpenseUser
import configparser
import webbrowser
import urllib
import threading
import tempfile
import os
import json
import datetime
import argparse

app = Flask(__name__)
oauth = OAuth(app)

parser = argparse.ArgumentParser(description="Add T-Mobile bill to Splitwise")
parser.add_argument("-c", "--config", required=True, help="path to .ini config file")
parser.add_argument("-e", "--expense", required=True, help="path to JSON expense file")
parser.add_argument("-d", "--dry-run", action='store_true', default=False, help="show what would happen without actually creating the expense on Splitwise")
args = parser.parse_args()

CONFIG_PATH = args.config
EXPENSE_PATH = args.expense
DRY_RUN = args.dry_run

# https://stackoverflow.com/questions/15562446/how-to-stop-flask-application-without-using-ctrl-c
def shutdown_server():
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        raise RuntimeError('Not running with the Werkzeug Server')
    func()


@app.route('/authorized', methods=['GET'])
def authorized():
    global state
    code = request.args.get('code')
    local_state = request.args.get('state')
    if local_state != state:
        return "Inconsistent state error! There's nothing you can really do about this.", 500
    access_token = s.getOAuth2AccessToken(code, redirect_uri)
    s.setOAuth2AccessToken(access_token)
    config = read_config(CONFIG_PATH)
    config['oauth']['access_token'] = json.dumps(access_token)
    with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
        config.write(tmp)

    os.rename(tmp.name, CONFIG_PATH)
    shutdown_server()
    return "Success! Server shutting down. This window can be closed.", 200

def read_config(filename):
    config = configparser.ConfigParser()
    config.read(filename)
    return config

def add_group_expense(exp_data: dict):
    """
    Utility function to add multiple expenses at once.
    Pass in a dict of user_ids -> amount owed
    """
    try:
        total = float(exp_data['total'])
        group_id = exp_data['group_id']
        desc = exp_data['desc']
    except KeyError:
        print("total, group_id and desc are required fields in the expense")
        return

    expense = Expense()
    expense.setCost(total)
    expense.setDescription(desc)
    expense.setGroupId(group_id)

    sum = 0

    for user_id, details in exp_data['users'].items():
        try:
            amt = float(details["amt"])
        except KeyError:
            print("'amt' is a required field")
            return
        except ValueError:
            print(f"invalid amount {details['amt']}")
            return

        user = ExpenseUser()
        user.setId(user_id)
        user.setPaidShare(0)
        user.setOwedShare(amt)
        sum += amt

        expense.addUser(user)

    me = ExpenseUser()
    me.setId(s.getCurrentUser().getId())
    me.setPaidShare(total)
    me.setOwedShare(total - sum)
    expense.addUser(me)

    if DRY_RUN:
        print("--- dry run ---")
        print_expense_details(expense)
        return

    resp, err = s.createExpense(expense)

    # handle err
    if err is not None:
        print(err.getErrors())
        return

    print("Expense created!")
    timestamp_comment = f"added at {datetime.datetime.now()} :)"
    add_comment_to_expense(resp.getId(), timestamp_comment)

    # extra comments
    if "comment" in exp_data and exp_data["comment"] != "":
        add_comment_to_expense(resp.getId(), comment)


def print_expense_details(expense: Expense):
    for user in expense.getUsers():
        print(f"{user.getId()} owes {user.getOwedShare()}")

def add_comment_to_expense(expense_id: id, comment: str):
    resp, err = s.createComment(expense_id, comment)
    return resp, err

if __name__ == "__main__":

    config = read_config(CONFIG_PATH)
    client_id = config['oauth']['client_id']
    client_secret = config['oauth']['client_secret']
    redirect_uri = config['oauth']['redirect_uri']
    server_addr = config['server']['addr']
    server_port = config.getint('server', 'port')
    server_debug = config.getboolean('server', 'debug')

    # TODO: initiate the oauth from a route instead of CLI to make this a web app
    s = Splitwise(client_id, client_secret)

    # see if we already have an access token, otherwise initialize the OAuth flow
    if config.has_option('oauth', 'access_token'):
        access_token = json.loads(config['oauth']['access_token'])
        s.setOAuth2AccessToken(access_token)
    else:
        print("No access token found. Initiating Splitwise integration")
        server_thread = threading.Thread(target=app.run, args=(server_addr, server_port, server_debug))
        server_thread.start()
        # TODO: use url_for in 'with app.app_context():'. requires setting SERVER_NAME env var
        redirect_uri = redirect_uri + '/authorized'
        url, state = s.getOAuth2AuthorizeURL(redirect_uri)
        webbrowser.open_new_tab(url)
        server_thread.join()

    with open(EXPENSE_PATH) as fp:
        exp_data = json.load(fp)

    # print(s.getCurrentUser().getId())
    add_group_expense(exp_data)
