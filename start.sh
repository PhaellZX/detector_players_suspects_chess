#!/bin/bash
chmod +x ./bin/stockfish
uvicorn main:app --host=0.0.0.0 --port=10000
