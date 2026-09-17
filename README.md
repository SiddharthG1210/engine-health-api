## Engine-Health_api
A FASTAPI-based system that does three things - 
1) predicts the remaining useful life(RUL) of an engine 
2) detects abnormal sensor behavior and flags it as an anomaly
3) And at out of 3 stages - healthy , warning , critical .. which phase it is in currently 

## Overview-
The traditional approach is scheduled maintenance — "replace parts every 6 months whether they need it or not." Wasteful and still doesn't prevent surprise failures. 
How the project solves this - Predictive maintenance
Instead of guessing, your system watches the engine's sensor data in real time and answers three questions:

How many more cycles does this engine have before it fails? → Plan maintenance exactly when needed, not too early, not too late
Is this engine behaving abnormally right now? → Catch problems the moment they start, not after failure
What wear stage is it in? → Give maintenance teams a simple "Healthy / Warning / Critical" signal they can act on immediately

## The three ML capabilities -
The project is built on three predictions, each solving a distinct problem-

**Remaining useful life (`predict_rul`)**
Takes 30 cycles of sensor readings. Feeds them through the trained LSTM neural network. Returns one number — how many cycles this engine has left before failure. This is the core prediction of the whole project.

**Anomaly score (`anomaly_score`)**
Takes one cycle of sensor readings. Feeds it through the Autoencoder — a network trained only on healthy engine data. Returns the reconstruction error and a true/false flag — is this engine behaving abnormally right now? High reconstruction error means the Autoencoder doesn't recognize this pattern as "normal healthy behavior."

**Degradation stage (`degradation_stage`)**
Takes 30 cycles of sensor readings. Uses the same LSTM as `predict_rul` to get the RUL, then applies a simple rule to classify the engine into one of three stages — Healthy (RUL > 100), Warning (RUL between 30-100), Critical (RUL < 30). Returns the stage number, the label, and the predicted RUL.

My RMSE: 15.77 cycles on official NASA test set. Anomaly detection catch rate: 63.5% on critical engines.

## How you reach them
You don't post raw sensor arrays any more — you **chat**, and an LLM decides which of the three predictions to run and on which engine. Sensor data is read server-side from the database, so nothing a user types becomes model input.

Two kinds of account, and they get different things:

- **Engineers** — all three capabilities, across the whole fleet. `POST /api/chat/engineer`, which returns the reply plus a list of exactly which tools ran on which engines.
- **Customers** — degradation stage only, and only on the engines assigned to them, answered in plain language with no jargon and no engine numbers. `POST /api/chat/customer`.

Supporting endpoints: `POST /api/auth/login` (form-encoded, returns a JWT), `GET /api/auth/me`, and engineer-only `POST /api/debug/predict-rul` / `/anomaly-score` / `/degradation-stage` for checking a chat answer against the raw prediction.

The old unauthenticated `/predict-rul`, `/anomaly-score` and `/degradation-stage` endpoints are gone on purpose. They let anyone read any engine by number, which would make the access rules above decorative.

### The version before this one

The original non-agentic build — a single `main.py` exposing those three endpoints directly, no auth, no chat — is kept on the [`v1-non-agentic`](../../tree/v1-non-agentic) branch rather than deleted. Same two models, same weights; the whole difference is what sits in front of them.

## Tech-stack
List: PyTorch, FastAPI, SQLAlchemy + SQLite, Google Gemini (`google-genai`), scikit-learn, NASA C-MAPSS dataset

## How to run it

**1. Install**
```
pip install -r requirements.txt
```

**2. Add the dataset.** Download the NASA C-MAPSS dataset from https://www.kaggle.com/datasets/behrad3d/nasa-cmaps and place `test_FD001.txt` and `RUL_FD001.txt` in the `data/` folder.

**3. Create a `.env`** in the project root:
```
GEMINI_API_KEY=your-google-ai-studio-key
JWT_SECRET_KEY=any-long-random-string
JWT_EXPIRE_MINUTES=60
```
The server refuses to start without the first two.

**4. Seed the database** — this creates `app.db`, picks the demo engines, and creates the demo accounts:
```
python -m app.db.seed
```
It prints a table of which engine went to which account. Re-running it is safe (it exits if already seeded); to start over, delete `app.db` and run it again.

**5. Start the server**
```
uvicorn app.main:app --reload
```
Then open http://127.0.0.1:8000/ for the web UI, or http://127.0.0.1:8000/docs for the API.

Run a **single worker** — chat history is kept in process memory, so multiple workers would give a user a different conversation on each request. History also resets whenever the server restarts; that's by design at this stage.

**Demo accounts** (all password `demo1234`): `engineer@demo.local`, and `customer1@demo.local` through `customer4@demo.local`. `customer4` is the interesting one — it owns two engines in opposite health states.

## Retraining the models
The API loads four artifacts from `ml_artifacts/`: `rul_model.pth`, `autoencoder_model.pth`, `anomaly_threshold.pkl`, and `scaler.pkl`. They're produced by `training.ipynb` — run that notebook top to bottom (it needs `train_FD001.txt` as well) and replace the four files.

Dataset: NASA C-MAPSS Turbofan Engine Degradation dataset
Download from: https://www.kaggle.com/datasets/behrad3d/nasa-cmaps
