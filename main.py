from fastapi import FastAPI
import torch
import torch.nn as nn
import numpy as np
import pandas as pd

app = FastAPI()

# Same LSTM architecture from Colab
class EngineRUL(nn.Module):
    def __init__(self):
        super().__init__()
        self.lstm = nn.LSTM(input_size=15, hidden_size=64, 
                           num_layers=2, batch_first=True, dropout=0.2)
        self.fc = nn.Linear(64, 1)
    
    def forward(self, x):
        out, _ = self.lstm(x)
        return self.fc(out[:, -1, :])

# Load the trained weights into that architecture
model = EngineRUL()
model.load_state_dict(torch.load('rul_model.pth'))
model.eval()

print("LSTM model loaded successfully")

FEATURE_COLS = [
    'sensor_2', 'sensor_3', 'sensor_4', 'sensor_6', 'sensor_7', 'sensor_8',
    'sensor_9', 'sensor_11', 'sensor_12', 'sensor_13', 'sensor_14',
    'sensor_15', 'sensor_17', 'sensor_20', 'sensor_21'
]

from pydantic import BaseModel
from typing import List

@app.get("/")
def home():
    return {"message": "Engine RUL API"}

class SensorWindow(BaseModel):
    sensor_data: List[List[float]]  # 30 cycles, 15 sensors each

@app.post("/predict-rul")
def predict_rul(window: SensorWindow):
    df_input = pd.DataFrame(window.sensor_data, columns=FEATURE_COLS)
    scaled_data = scaler.transform(df_input)
    input_tensor = torch.FloatTensor(scaled_data).unsqueeze(0)  # (1, 30, 15
    
    # scaled_data = scaler.transform(np.array(window.sensor_data))
    # input_tensor = torch.FloatTensor([scaled_data])
    
    with torch.no_grad():
        prediction = model(input_tensor).item()
    
    return {
        "predicted_rul": round(prediction, 2),
  
    }

    


    


class EngineAutoencoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(15, 8), nn.ReLU(), nn.Linear(8, 4)
        )
        self.decoder = nn.Sequential(
            nn.Linear(4, 8), nn.ReLU(), nn.Linear(8, 15)
        )
    
    def forward(self, x):
        return self.decoder(self.encoder(x))

import joblib
autoencoder = EngineAutoencoder()
autoencoder.load_state_dict(torch.load('autoencoder_model.pth'))
autoencoder.eval()

threshold = joblib.load('anomaly_threshold.pkl')

class SensorRow(BaseModel):
    sensor_data: List[float]  # just 1 cycle, 15 sensors

@app.post("/anomaly-score")
def anomaly_score(row: SensorRow):
    df_input = pd.DataFrame([row.sensor_data], columns=FEATURE_COLS)  # 1 row x 15 cols
    scaled_data = scaler.transform(df_input)  # shape (1, 15)
    input_tensor = torch.FloatTensor(scaled_data)  # shape (1, 15)
    
    with torch.no_grad():
        rebuilt = autoencoder(input_tensor)
        error = torch.mean((rebuilt - input_tensor)**2).item()
    
    is_anomaly = error > threshold
    
    return {
        "reconstruction_error": round(error, 6),
        "threshold": round(float(threshold), 6),
        "is_anomaly": bool(is_anomaly)
    }

@app.post("/degradation-stage")
def degradation_stage(window: SensorWindow):

    df_input = pd.DataFrame(window.sensor_data, columns=FEATURE_COLS)
    scaled_data = scaler.transform(df_input)
    input_tensor = torch.FloatTensor(scaled_data).unsqueeze(0)  # (1, 30, 15)

    
    with torch.no_grad():
        rul = model(input_tensor).item()
    
    if rul > 100:
        stage = 0
        label = "Healthy"
    elif rul > 30:
        stage = 1
        label = "Warning"
    else:
        stage = 2
        label = "Critical"
    
    return {
        "predicted_rul": round(rul, 2),
        "degradation_stage": stage,
        "label": label
    }

scaler = joblib.load('scaler.pkl')


print("All models loaded successfully")