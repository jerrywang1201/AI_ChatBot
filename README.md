⸻

Audio Diags Bot – Setup Guide

This repository provides a local chatbot system for Apple Audio Diagnostics. It integrates Radar DB, Codebase DB, genai-bridge, a FastAPI backend, and an Open WebUI frontend.

⸻

1. Clone packages

• cd folder

• git clone https://github.com/tree-sitter/tree-sitter-cpp

• cd data/scripts

• git clone ssh://git@stash.sd.apple.com/audio/appleapfactorytest.git

⸻

2. Create virtual environment (Python 3.11+ recommended)

• python3 -m venv venv


⸻

3. Activate environment

• source venv/bin/activate


⸻

4. Install dependencies

• pip install -r requirements.txt


⸻

5. Build Radar DB

•Run the Radar → Qdrant indexer:

• python3 radar/radar_to_qdrant.py \
   -c 1345828 1711116 1533629 1345830 1533631 \
   --collection radar_index \
   --recreate


⸻

6. Build Codebase DB

• Run the Repo AST → Qdrant indexer:

  python3 utils/repo_ast_to_qdrant.py --repo /Users/jialongwangsmacbookpro16/Desktop/chatbot/data/scripts/appleapfactorytest


⸻

7. Run genai-bridge

• Download Enchante:
  👉 genai-bridge releases

• Open the app:

  Connect the port to:
  👉 http://localhost:11211

⸻

8. Run the Backend (FastAPI + Uvicorn)

• Start the backend server:

  uvicorn backend.app:app --reload --port 8002

  Backend will be available at:
  👉 http://localhost:8002/v1/

⸻

9. Run the Frontend (Open WebUI)

• docker run -d --name open-webui \
   -e ENABLE_OPENWEB_API=true \
   -e OPENWEB_API_BASE_URL=http://host.docker.internal:8002/v1 \
   -e OPENWEB_API_KEY=dev-anything \
   -p 3000:8080 \
   ghcr.io/open-webui/open-webui:main

   Frontend will be available at:
   👉 http://localhost:3000

⸻

## Project Structure

```bash
├── __pycache__
│   ├── interlinked_ai.cpython-313.pyc
│   ├── interlinked_local.cpython-311.pyc
│   ├── interlinked_local.cpython-313.pyc
│   └── interlinked.cpython-313.pyc
├── ai - AI client and Apple Interlinked integration
│   ├── __pycache__
│   ├── ai_client_factory.py
│   └── my_interlinked_core.py
├── audiotool - Future development for Audio Diagnostics Bot (using AudioFactoryDiagsTools)
│   ├──  __init__.py
│   ├── __pycache__
│   ├── audio_search.py
│   └── index.py
├── backend - Audio Diags Bot backend scripts
│   ├── __init__.py
│   ├── __init__.pyc
│   ├── __pycache__
│   ├── app.py
│   ├── chat_router.py
│   ├── code_search_tool.py
│   ├── deps
│   ├── prompt_templates
│   ├── radar_analysis.py
│   ├── radar_searcher.py
│   ├── script_index.py
│   └── unified_search.py
├── build 
│   └── my-languages.so
├── build_lang.py - Building the Tree-sitter Language Parsing Library
├── code - Local virtual environment, non-source code
│   ├── bin
│   ├── include
│   ├── lib
│   ├── pyvenv.cfg
│   └── share
├── codetest - Repo vector database testing scripts
│   ├── __pycache__
│   ├── analyze_fn_to_json.py
│   ├── json_test.py
│   ├── output
│   └── quick_search.py
├── data - repo database
│   └── scripts
├── docker-compose.yml
├── Dockerfile
├── interlinked_local.py 
├── qdrant_data
│   ├── aliases
│   ├── collections
│   └── raft_state.json
├── radar - Radar embedding into vector database and validation scripts
│   ├── __init__.py
│   ├── __pycache__
│   ├── debug.py
│   ├── quick_radar_check.py
│   ├── radar_api_sanity.py
│   ├── radar_description.py
│   ├── radar_to_qdrant.py
│   └── validate_radar.py
├── README.md
├── requirements-extras.txt
├── requirements_all.txt
├── requirements.txt 
└── utils - AST extraction and embedding into Qdrant vector database
    ├── __init__.py
    ├── __pycache__
    ├── ast_extractor.py
    ├── embedder.py
    ├── qdrant_helper.py
    └── repo_ast_to_qdrant.py