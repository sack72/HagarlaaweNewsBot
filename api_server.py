from fastapi import FastAPI
from session_analytics import aggregate_session_sentiment

app = FastAPI()

@app.get("/api/session-sentiment")
def get_session_sentiment():
    return aggregate_session_sentiment()

@app.get("/")
def root():
    return {"status": "Hagarlaawe HMM API is running"}
from session_analytics import generate_somali_session_summary

@app.get("/api/session-summary")
def get_somali_session_summary():
    return {"summary": generate_somali_session_summary()}
