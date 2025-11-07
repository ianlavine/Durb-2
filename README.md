Graph demo (Python backend + Phaser frontend)

Prereqs

- Python 3.9+

Install

```bash
pip install -r requirements.txt
```

Generate a planar graph

1) Edit top of `generate_graph.py` for counts and 1-way percent if desired
2) Run:

```bash
python backend/generate_graph.py
```

Run the websocket server

```bash
python -m backend.server
```

- Server listens on `ws://localhost:8765`
- It streams an initial graph snapshot, then compact tick updates every 0.1s
- Click a node on the frontend to claim it for Player 1

Open the frontend

- Open `frontend/index.html` directly in a browser
  - If opened via `file://`, it will default to `ws://localhost:8765`
  - If hosted, it will connect to the page's hostname

Controls

- Left-click a node: claim it for Player 1 (if unowned)
- Left-click an edge: toggle flow. If turning on, requires from-node owned by Player 1
- Right-click a 2-way edge: swap its current direction
- PLAY button: generates a new graph and starts a game

Sandbox tester

1. In one terminal, run the resolver so the sandbox can call the real backend logic:

```bash
python -m backend.sharp_angle_service --port 5051
```

2. Open `frontend/sandbox.html` directly in a browser. Click empty space to spawn nodes; click one node then another to add a pipe.
3. Use **Generate Graph** to load a fresh sparse map produced by the backend generator (existing content is cleared, but you can still add custom nodes afterward).
4. Each new pipe sends the current board to the resolver, which uses `sharp_angle_handler.py` to relax angles. Movements are animated on-screen and listed in the left-hand log. If a new pipe crosses existing ones, those old pipes are removed just like in the real game.
5. Use the Reset button in the top-right to clear the canvas.

Troubleshooting

- Black screen: ensure `server.py` is running and `graph.json` exists
- Console logs: open DevTools (Console) to see websocket status messages
- Firewall: allow inbound connections to port 8765 if testing across devices
