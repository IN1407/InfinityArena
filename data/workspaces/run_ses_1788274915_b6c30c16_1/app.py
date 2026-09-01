import pickle, os
from flask import Flask, request
app = Flask(__name__)

@app.route('/load')
def load():
    data = request.args.get('data')
    return pickle.loads(bytes.fromhex(data))   # unsafe deserialization

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')        # debug=True in prod is risky
