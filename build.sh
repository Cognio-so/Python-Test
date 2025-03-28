#!/bin/bash
pip install -r requirements.txt 
pip install -e .
mkdir -p dist
cp main.py dist/
# Create a worker script that handles CORS preflight requests
echo 'export default {
  async fetch(request, env, ctx) {
    // Handle CORS preflight requests
    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "Access-Control-Allow-Origin": "https://vanni-test-frontend.vercel.app",
          "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
          "Access-Control-Allow-Headers": "Content-Type, Authorization",
          "Access-Control-Max-Age": "86400"
        }
      });
    }
    
    // Pass through to Python function
    return await env.PYTHON.fetch(request);
  }
}' > dist/_worker.js
