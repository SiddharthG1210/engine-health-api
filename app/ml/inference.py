"""ML serving core: loads artifacts once at import, exposes pure numpy-in functions.

Behavior (scaling, architectures, thresholds) is unchanged from the original
root main.py -- this is a relocation, not a rewrite. Unlike that file, every
artifact is loaded here *before* the functions that need them are defined, so
there is no reliance on FastAPI deferring route bodies past import time.
"""

import joblib
import numpy as np
import pandas as pd
import torch

from app.config import ML_ARTIFACTS_DIR
from app.ml.architectures import EngineAutoencoder, EngineRUL
from app.ml.feature_columns import FEATURE_COLS

# --- Load once, at import ---

scaler = joblib.load(ML_ARTIFACTS_DIR / "scaler.pkl")

rul_model = EngineRUL()
rul_model.load_state_dict(torch.load(ML_ARTIFACTS_DIR / "rul_model.pth"))
rul_model.eval()

autoencoder = EngineAutoencoder()
autoencoder.load_state_dict(torch.load(ML_ARTIFACTS_DIR / "autoencoder_model.pth"))
autoencoder.eval()

anomaly_threshold = joblib.load(ML_ARTIFACTS_DIR / "anomaly_threshold.pkl")


# --- Pure inference functions ---

def predict_rul(raw_window: np.ndarray) -> float:
    """raw_window: (30, 15) raw/unscaled, columns in FEATURE_COLS order."""
    df_input = pd.DataFrame(raw_window, columns=FEATURE_COLS)
    scaled_data = scaler.transform(df_input)
    input_tensor = torch.FloatTensor(scaled_data).unsqueeze(0)  # (1, 30, 15)

    with torch.no_grad():
        prediction = rul_model(input_tensor).item()

    return round(prediction, 2)


def anomaly_score(raw_cycle: np.ndarray) -> dict:
    """raw_cycle: (15,) raw/unscaled, in FEATURE_COLS order."""
    df_input = pd.DataFrame([raw_cycle], columns=FEATURE_COLS)  # 1 row x 15 cols
    scaled_data = scaler.transform(df_input)  # (1, 15)
    input_tensor = torch.FloatTensor(scaled_data)  # (1, 15)

    with torch.no_grad():
        rebuilt = autoencoder(input_tensor)
        error = torch.mean((rebuilt - input_tensor) ** 2).item()

    is_anomaly = error > anomaly_threshold

    return {
        "reconstruction_error": round(error, 6),
        "threshold": round(float(anomaly_threshold), 6),
        "is_anomaly": bool(is_anomaly),
    }


def degradation_stage(raw_window: np.ndarray) -> dict:
    """raw_window: (30, 15) raw/unscaled, columns in FEATURE_COLS order."""
    df_input = pd.DataFrame(raw_window, columns=FEATURE_COLS)
    scaled_data = scaler.transform(df_input)
    input_tensor = torch.FloatTensor(scaled_data).unsqueeze(0)  # (1, 30, 15)

    with torch.no_grad():
        rul = rul_model(input_tensor).item()

    # Thresholds applied to the raw (unrounded) prediction, same as the
    # original main.py -- matches predict_rul()'s own rounding only in the output.
    if rul > 100:
        stage, label = 0, "Healthy"
    elif rul > 30:
        stage, label = 1, "Warning"
    else:
        stage, label = 2, "Critical"

    return {
        "predicted_rul": round(rul, 2),
        "degradation_stage": stage,
        "label": label,
    }
