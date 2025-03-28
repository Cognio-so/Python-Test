#!/bin/bash
pip install -r requirements.txt 
pip install -e .
mkdir -p dist
cp main.py dist/
