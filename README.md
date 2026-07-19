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

## The end-points -
The project has three end points ,each solving a distinct problems mentioned- 

POST /predict-rul
Takes 30 cycles of sensor readings as input. Feeds it through the trained LSTM neural network. Returns one number — how many cycles this engine has left before failure. This is the core prediction of the whole project.

POST /anomaly-score
Takes one cycle of sensor readings. Feeds it through the LSTM Autoencoder — a network trained only on healthy engine data. Returns the reconstruction error and a true/false flag — is this engine behaving abnormally right now? High reconstruction error means the Autoencoder doesn't recognize this pattern as "normal healthy behavior."

POST /degradation-stage
Takes 30 cycles of sensor readings. Uses the same LSTM as /predict-rul to get the RUL, then applies a simple rule to classify the engine into one of three stages — Healthy (RUL > 100), Warning (RUL between 30-100), Critical (RUL < 30). Returns the stage number, the label, and the predicted RUL.

My RMSE: 15.77 cycles on official NASA test set. Anomaly detection catch rate: 63.5% on critical engines.
RUN THE CELLS IN THE COLAB FILE PROVIDED  TO CREATE pkl and pth files and replace them in the main code

## Tech-stack
List: PyTorch, FastAPI, scikit-learn, NASA C-MAPSS dataset

## How to run it
pip install -r requirements.txt
uvicorn main:app --reload



## Dataset: NASA C-MAPSS Turbofan Engine Degradation dataset
Download from: https://www.kaggle.com/datasets/behrad3d/nasa-cmaps
Place the txt files in the project folder before running