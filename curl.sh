#!/bin/bash

for i in {1..2}; do
    echo "=== Run $i ==="
    curl http://172.18.0.170:5000/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer EMPTY" -d '{
    "model": "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
    "messages": [
      {"role": "user", "content": "Hello, are you working?"}
    ],
    "max_tokens": 100,
    "stream": true 
  }'
    echo "\n"
done
