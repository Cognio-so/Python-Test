#!/bin/bash
pip install -r requirements.txt 
pip install -e .
mkdir -p dist
cp main.py dist/
echo "/*
  Access-Control-Allow-Origin: https://vanni-test-frontend.vercel.app
  Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS
  Access-Control-Allow-Headers: Content-Type, Authorization
  Access-Control-Max-Age: 86400" > dist/_headers
