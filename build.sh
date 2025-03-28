#!/bin/bash
pip install -r requirements.txt 
pip install -e .
mkdir -p dist
cp main.py dist/index.py
cp -r static dist/ 2>/dev/null || true
cp -r templates dist/ 2>/dev/null || true
echo '
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import os

# Import the app from main.py
from index import app as main_app

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://vanni-test-frontend.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include the routes from main_app
app.include_router(main_app.router)

# Handle OPTIONS requests explicitly
@app.options("/{path:path}")
async def options_route(request: Request, path: str):
    return {}
' > dist/_worker.js
