"""Single source of truth for the 15 sensor columns the models were trained on.

Order is load-bearing -- it must match the column order `scaler.pkl` was fit
on in training.ipynb (after dropping constant sensors 1, 5, 10, 16, 18, 19).
Do not reorder or "clean up" this list.
"""

FEATURE_COLS: list[str] = [
    "sensor_2", "sensor_3", "sensor_4", "sensor_6", "sensor_7", "sensor_8",
    "sensor_9", "sensor_11", "sensor_12", "sensor_13", "sensor_14",
    "sensor_15", "sensor_17", "sensor_20", "sensor_21",
]
