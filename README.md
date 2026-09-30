KBC Whisper — Proof of Concept (Frontend + Python Backend)
Whisper is a concept demo for KBC Mobile that detects key financial moments (booking a flight, buying a car, paying a rental deposit, or receiving a year-end bonus) and offers a single, non-intrusive suggestion for matching insurance cover or smart financial moves.
This repository contains the interactive mobile UI frontend connected to a lightweight Python (Flask) backend that handles transaction classification, partner data consent, real-time quote pricing, and policy checkout.


📁 Project Structure


Make sure your files are arranged in the following structure (Flask requires `index.html` to be inside a `templates/` folder):
```text
kbc-whisper-poc/
├── app.py # Python Flask backend & pricing engine
├── README.md # Project documentation
└── templates/
└── index.html # Interactive frontend UI + API bridge
