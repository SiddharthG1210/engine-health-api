"""Model architectures, moved verbatim from the original root main.py.

These must stay structurally identical to the classes of the same name in
training.ipynb -- load_state_dict fails (or silently mismatches) if they drift.
"""

import torch.nn as nn


class EngineRUL(nn.Module):
    """2-layer LSTM regressor: (batch, 30, 15) -> predicted RUL."""

    def __init__(self):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=15, hidden_size=64,
            num_layers=2, batch_first=True, dropout=0.2,
        )
        self.fc = nn.Linear(64, 1)

    def forward(self, x):
        out, _ = self.lstm(x)
        return self.fc(out[:, -1, :])


class EngineAutoencoder(nn.Module):
    """15 -> 8 -> 4 -> 8 -> 15 autoencoder, trained only on healthy-engine rows."""

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
