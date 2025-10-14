"""
Bot game management: contains `BotGameManager` and global `bot_game_manager`.
This module is responsible for creating a game, wiring a bot into it, and
exposing simple controls for ticking and lifecycle.
"""

from typing import Optional, Tuple

from .game_engine import GameEngine
from .bots import Bot2, BotPop1
from .agent import Agent, AgentLite
from .constants import DEFAULT_GAME_MODE, normalize_game_mode, PLAYER_COLOR_SCHEMES
import random
import time


class BotGameManager:
    """Manages bot vs AI games."""

    def __init__(self):
        self.game_engine = GameEngine()
        self.bot_player: Optional[BotPlayer] = None
        self.human_token: Optional[str] = None
        self.game_active = False
        # Last client-facing event from the most recent bot action
        self.last_client_event: Optional[dict] = None

    def start_bot_game(
        self,
        difficulty: str = "easy",
        auto_expand: bool = False,
        mode: str = DEFAULT_GAME_MODE,
    ) -> Tuple[bool, Optional[str]]:
        """
        Start a new bot vs human game with specified difficulty.
        Returns: (success, error_message)
        """
        try:
            mode = normalize_game_mode(mode)

            # Create bot player with specified difficulty
            self.bot_player = Bot2(player_id=2, color="#ff3344", difficulty=difficulty)
            self.ai_player = AgentLite(player_id=1, color="#3388ff")
            self.ai_player.bot_token += "1"
            self.ai_token = self.ai_player.bot_token

            player_slots = [
                {
                    "player_id": 1,
                    "token": self.ai_player.bot_token,
                    "color": PLAYER_COLOR_SCHEMES[0]["color"],
                    "secondary_colors": PLAYER_COLOR_SCHEMES[0]["secondary"],
                    "auto_expand": False,
                },
                {
                    "player_id": 2,
                    "token": self.bot_player.bot_token,
                    "color": PLAYER_COLOR_SCHEMES[1]["color"],
                    "secondary_colors": PLAYER_COLOR_SCHEMES[1]["secondary"],
                    "auto_expand": True,
                },
            ]

            self.game_engine.start_game(player_slots, mode=mode)
            self.bot_player.join_game(self.game_engine)
            self.ai_player.join_game(self.game_engine)
            self.game_engine._try_start_play_phase()
            self.game_engine.state.start_game_timer(time.time())
            self.game_active = True

            return True, None

        except Exception as e:
            print(e)
            return False, str(e)

    async def make_bot_move(self) -> bool:
        """Make the bot's move if it's the bot's turn."""
        if not self.bot_player or not self.game_active:
            return False

        # Reset previous event
        self.last_client_event = None

        # Capture pre-state snapshot to detect changes
        state = self.game_engine.state
        pre_edges = None
        if state:
            pre_edges = {eid: (e.source_node_id, e.target_node_id, e.on, e.flowing)
                         for eid, e in state.edges.items()}

        moved = await self.bot_player.make_move()

        # Derive a client event from state deltas if we detect a change
        if moved and state:
            post_edges = {eid: (e.source_node_id, e.target_node_id, e.on, e.flowing)
                          for eid, e in state.edges.items()}
            # New edges (bridge builds)
            for eid, tup in post_edges.items():
                if pre_edges is None or eid not in pre_edges:
                    e = state.edges.get(eid)
                    if e:
                        self.last_client_event = {
                            "type": "newEdge",
                            "edge": {
                                "id": e.id,
                                "source": e.source_node_id,
                                "target": e.target_node_id,
                                "bidirectional": False,
                                "forward": True,
                                "on": e.on,
                                "flowing": e.flowing,
                                "building": bool(getattr(e, 'building', False)),
                                "buildTicksRequired": int(getattr(e, 'build_ticks_required', 0)),
                                "buildTicksElapsed": int(getattr(e, 'build_ticks_elapsed', 0)),
                            },
                        }
                        break
            # Only emit 'edgeReversed' for true direction swaps; otherwise 'edgeUpdated'
            if not self.last_client_event:
                for eid, before in (pre_edges or {}).items():
                    after = post_edges.get(eid)
                    if not after:
                        continue
                    before_src, before_tgt, before_on, before_flowing = before
                    after_src, after_tgt, after_on, after_flowing = after
                    e = state.edges.get(eid)
                    if not e:
                        continue
                    # True reversal when source/target swap places
                    reversed_dir = (after_src == before_tgt and after_tgt == before_src)
                    if reversed_dir:
                        self.last_client_event = {
                            "type": "edgeReversed",
                            "edge": {
                                "id": e.id,
                                "source": e.source_node_id,
                                "target": e.target_node_id,
                                "bidirectional": False,
                                "forward": True,
                                "on": e.on,
                                "flowing": e.flowing,
                            },
                        }
                        break
                    # Otherwise, if state changed, emit a standard update
                    on_changed = (before_on != after_on)
                    flowing_changed = (before_flowing != after_flowing)
                    if on_changed or flowing_changed:
                        self.last_client_event = {
                            "type": "edgeUpdated",
                            "edge": {
                                "id": e.id,
                                "on": e.on,
                                "flowing": e.flowing,
                            },
                        }
                        break

        return moved

    async def make_ai_move(self, action) -> bool:
        """Make the bot's move if it's the bot's turn."""
        if not self.ai_player or not self.game_active:
            return False

        # Reset previous event
        self.last_client_event = None

        # Capture pre-state snapshot to detect changes
        state = self.game_engine.state
        pre_edges = None
        if state:
            pre_edges = {eid: (e.source_node_id, e.target_node_id, e.on, e.flowing)
                         for eid, e in state.edges.items()}
            
        action = self.ai_player.actions_dict[action]
        moved = await self.ai_player.step(action)

        # Derive a client event from state deltas if we detect a change
        if moved and state:
            post_edges = {eid: (e.source_node_id, e.target_node_id, e.on, e.flowing)
                          for eid, e in state.edges.items()}
            # New edges (bridge builds)
            for eid, tup in post_edges.items():
                if pre_edges is None or eid not in pre_edges:
                    e = state.edges.get(eid)
                    if e:
                        self.last_client_event = {
                            "type": "newEdge",
                            "edge": {
                                "id": e.id,
                                "source": e.source_node_id,
                                "target": e.target_node_id,
                                "bidirectional": False,
                                "forward": True,
                                "on": e.on,
                                "flowing": e.flowing,
                                "building": bool(getattr(e, 'building', False)),
                                "buildTicksRequired": int(getattr(e, 'build_ticks_required', 0)),
                                "buildTicksElapsed": int(getattr(e, 'build_ticks_elapsed', 0)),
                            },
                        }
                        break
            # Only emit 'edgeReversed' for true direction swaps; otherwise 'edgeUpdated'
            if not self.last_client_event:
                for eid, before in (pre_edges or {}).items():
                    after = post_edges.get(eid)
                    if not after:
                        continue
                    before_src, before_tgt, before_on, before_flowing = before
                    after_src, after_tgt, after_on, after_flowing = after
                    e = state.edges.get(eid)
                    if not e:
                        continue
                    # True reversal when source/target swap places
                    reversed_dir = (after_src == before_tgt and after_tgt == before_src)
                    if reversed_dir:
                        self.last_client_event = {
                            "type": "edgeReversed",
                            "edge": {
                                "id": e.id,
                                "source": e.source_node_id,
                                "target": e.target_node_id,
                                "bidirectional": False,
                                "forward": True,
                                "on": e.on,
                                "flowing": e.flowing,
                            },
                        }
                        break
                    # Otherwise, if state changed, emit a standard update
                    on_changed = (before_on != after_on)
                    flowing_changed = (before_flowing != after_flowing)
                    if on_changed or flowing_changed:
                        self.last_client_event = {
                            "type": "edgeUpdated",
                            "edge": {
                                "id": e.id,
                                "on": e.on,
                                "flowing": e.flowing,
                            },
                        }
                        break

        return moved
    
    def get_game_engine(self) -> GameEngine:
        """Get the game engine for the bot game."""
        return self.game_engine

    def end_game(self) -> None:
        """End the bot game."""
        self.game_active = False
        self.bot_player = None
        self.human_token = None
        self.game_engine._end_game()
