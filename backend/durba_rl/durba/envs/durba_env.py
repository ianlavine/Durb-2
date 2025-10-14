# dozenstones_env/envs/gohti_env.py
from enum import Enum

import numpy as np
import math
import pygame
from pygame.locals import *

import gymnasium as gym
from gymnasium import spaces
from .game_engine import GameEngine
from .bots import Bot2
from .bot_manager import BotGameManager
from .constants import PASSIVE_INCOME_ENABLED, NODE_MAX_JUICE, GAME_TIME, TICK_INTERVAL_SECONDS
from .rew_shaper import RewardShaping
from .generate_graph import NODE_COUNT
from .custom_monitor import CustomMonitor
import time
import asyncio

class DurbaEnv(gym.Env):
    metadata = {"render_modes": ["human", "rgb_array"], "render_fps": 4}

    def __init__(self, obs_type = "vector", act_type="single_discrete", render_mode=None):

        self.size = int(NODE_COUNT)  # The number of nodes
        self.n_edges_max = int(self.size*(self.size-1)/2)
        self.window_size = (2048, 1024)
        self.MAX_STEPS = 500
        self.game_manager = BotGameManager()
        self.rew_shape = RewardShaping(self.size, self.n_edges_max, self.MAX_STEPS)
        self.game_manager.start_bot_game()
        self.obs_type = obs_type
        self.act_type = act_type
        self.n_actions = len(self.game_manager.ai_player._calc_actions_space().keys())
        self.observation_space = self.get_obs_space()
        self.action_space = self.get_act_space()

        assert render_mode is None or render_mode in self.metadata["render_modes"]
        self.render_mode = render_mode

        self.window = None
        self.clock = None
        
    
    def _get_obs(self):
        """Get current observation"""
        state = self.game_manager.game_engine.state

        if self.obs_type == "vector":
            nodes_features = np.zeros((int(self.size*7)), dtype=np.float32)
            edges_features = np.zeros((int(self.size*(self.size-1)/2*5)), dtype=np.float32)
            global_features = np.zeros((3), dtype=np.float32)

            nodes = state.nodes
            screen = self.game_manager.game_engine.screen
            x_max = screen["width"]
            y_max = screen["height"]
            for node_id, node in nodes.items():
                nodes_features[node_id*7+0] = (node.x-screen["minX"])/x_max
                nodes_features[node_id*7+1] = (node.y-screen["minY"])/y_max
                nodes_features[node_id*7+2] = node.juice/NODE_MAX_JUICE
                nodes_features[node_id*7+3] = node.cur_intake
                if node.owner:
                    if node.owner == 1:
                        nodes_features[node_id*7+4] = 1.0
                        nodes_features[node_id*7+5] = 0.0
                        nodes_features[node_id*7+6] = 0.0
                    else:
                        nodes_features[node_id*7+4] = 0.0
                        nodes_features[node_id*7+5] = 1.0
                        nodes_features[node_id*7+6] = 0.0
                else:
                    nodes_features[node_id*7+4] = 0.0
                    nodes_features[node_id*7+5] = 0.0
                    nodes_features[node_id*5+6] = 1.1
            edges = state.edges
            for node_id in range(0, len(nodes.values())-1):
                for tgt_node_id in range(node_id+1, len(nodes.values())):
                    node = nodes[node_id]
                    tgt_node = nodes[tgt_node_id]
                    edge_id = list(set(node.attached_edge_ids)&set(tgt_node.attached_edge_ids))
                    if edge_id:
                        edge = edges[edge_id[0]]
                        if edge.source_node_id==node_id:
                            edges_features[node_id*5+0] = 1.0
                            edges_features[node_id*5+1] = 0.0
                        else:
                            edges_features[node_id*5+0] = 0.0
                            edges_features[node_id*5+1] = 1.0
                        edges_features[node_id*4+2] = edge.on
                        edges_features[node_id*4+3] = edge.flowing
                        edges_features[node_id*4+4] = edge.last_transfer
            
            global_features[0] = state.player_gold[1]/300
            global_features[1] = state.player_gold[2]/300
            if state.game_start_time is not None:
                elapsed = max(0.0, time.time() - state.game_start_time)
                timer_remaining = max(0.0, state.game_duration - elapsed)

            global_features[2] = timer_remaining/(GAME_TIME*60)
            obs = {"nodes": nodes_features, "edges": edges_features, "global": global_features}
        elif self.obs_type == "gnn":
            node_features = np.zeros((self.size, 5), dtype=np.float32)
            node_mask = np.zeros(self.size, dtype=np.float32)
            max_edges = int(self.size*(self.size-1)/2)
            edge_features = np.zeros((max_edges, 8), dtype=np.float32)
            edge_index = np.zeros((2, max_edges), dtype=np.int64)
            edge_mask = np.zeros(max_edges, dtype=np.float32)
            global_features = np.zeros((3), dtype=np.float32)

            nodes = state.nodes
            edges = state.edges
            screen = self.game_manager.game_engine.screen
            #x_max = screen["width"]
            #y_max = screen["height"]
            #x_min = screen["minX"]
            #y_min = screen["minY"]
            for i, n in nodes.items():
                node_features[i] = [
                    #(n.x-x_min)/x_max,
                    #(n.y-y_min)/y_max,
                    n.juice,
                    float(1 if n.owner is None else 0),
                    float(1 if n.owner==1 else 0),
                    float(1 if n.owner==2 else 0),
                    n.cur_intake
                ]
                node_mask[i] = 1.0  # mark as valid node

            # Build edge index (2 x num_edges)
            for j, e in edges.items():
                edge_index[:, j] = [e.source_node_id, e.target_node_id]
                cost = self.game_manager.game_engine.calculate_bridge_cost(state.nodes[e.source_node_id], state.nodes[e.target_node_id])
                edge_features[j] = [
                    float(e.on),
                    float(e.flowing),
                    e.last_transfer,
                    e.build_ticks_required,
                    e.build_ticks_elapsed,
                    float(e.building),
                    float(cost),
                    float(cost<state.player_gold[1])
                ]
                edge_mask[j] = 1.0  # mark as valid edge
            global_features[0] = state.player_gold[1]
            global_features[1] = state.player_gold[2]
            global_features[2] = (self.MAX_STEPS-self.n_steps)/self.MAX_STEPS
            obs = {
                "x": node_features,
                "edge_index": edge_index,
                "edge_attr": edge_features,
                "node_mask": node_mask,
                "edge_mask": edge_mask,
                "global": global_features
            }
        return obs
    
    def _get_info(self):
        return {
        }
    
    def get_obs_space(self):
        """Get current observation space based on given obs_type"""
        if self.obs_type == "matrix_6c":
            observation_space = spaces.Dict({
                "nodes": spaces.Box(low=0, high=1, shape=(6, self.size, self.size), dtype=np.int8),
                "global": spaces.Box(low=0, high=1, shape=(2,), dtype=np.float32),
            })
            return observation_space
        elif self.obs_type == "multi_input":
            observation_space = spaces.Dict({
                "edges": spaces.Box(low=0, high=1, shape=(4, self.size, self.size), dtype=np.uint8),
                "nodes": spaces.Box(low=-np.inf, high=np.inf, shape=(5,), dtype=np.float32),
                "global": spaces.Box(low=0, high=1, shape=(2,), dtype=np.float32),
            })
            return observation_space
        elif self.obs_type == "vector":
            observation_space = spaces.Dict({
                "nodes": spaces.Box(low=0, high=1, shape=(int(self.size*7), ), dtype=np.float32),
                "edges": spaces.Box(low=0, high=1, shape=(int(self.size*(self.size-1)/2*5), ), dtype=np.float32),
                "global": spaces.Box(low=0, high=1, shape=(3,), dtype=np.float32),
            })
            return observation_space
        elif self.obs_type == "gnn":
            MAX_NODES = self.size
            MAX_EDGES = int(self.size*(self.size-1)/2)
            return spaces.Dict({
                "x": spaces.Box(low=-np.inf, high=np.inf, shape=(MAX_NODES, 5), dtype=np.float32),
                "edge_index": spaces.Box(low=0, high=MAX_NODES, shape=(2, MAX_EDGES), dtype=np.int64),
                "edge_attr": spaces.Box(low=-np.inf, high=np.inf, shape=(MAX_EDGES, 8), dtype=np.float32),
                "node_mask": spaces.Box(low=0, high=1, shape=(MAX_NODES,), dtype=np.float32),
                "edge_mask": spaces.Box(low=0, high=1, shape=(MAX_EDGES,), dtype=np.float32),
                "global": spaces.Box(low=0, high=1, shape=(3,), dtype=np.float32)
            })
    
    def get_act_space(self):
        """Get current action_space in gym format based on given act_type"""
        if self.act_type == "single_discrete":
            return spaces.Discrete(self.n_actions)
        
        if self.act_type == "multi_discrete":
            return spaces.MultiDiscrete([4, self.n_edges_max])
        
    def valid_action_mask(self):
        """Get current step valid actions mask """
        if self.act_type == "single_discrete":
            # Create zero mask vector of size n_actions
            mask = np.zeros((self.n_actions), dtype=np.int8)
            # Get all valid action
            try:
                valid_act = list(map(lambda act: self.game_manager.ai_player.ind_2_act[act], self.game_manager.ai_player.get_valid_actions()))
            except Exception as e:
                exit()
            # Set all valid actions to 1
            mask[valid_act] = 1
            return mask
        else:
            # Create zero mask vector of size n_actions
            mask = np.zeros((self.n_actions), dtype=np.int8)
            # Get all valid action
            try:
                valid_act = list(map(lambda act: self.game_manager.ai_player.ind_2_act[act], self.game_manager.ai_player.get_valid_actions()))
            except Exception as e:
                exit()
            # Set all valid actions to 1
            mask[valid_act] = 1
            return mask
    
    def reset(self, seed=None, options=None):
        """Reset the environment"""
        super().reset(seed=seed)
        self.game_manager = BotGameManager()
        self.game_manager.start_bot_game()
        asyncio.run(self.game_manager.make_bot_move())
        self.rew_shape.reset()
        self.n_steps = 0
        self.n_agent_nodes = 0
        self.n_opponent_nodes = 0
        self.agent_gold_reserves = 0

        observation = self._get_obs()
        info = self._get_info()

        if self.render_mode == "human":
            self._render_frame()

        return observation, info
    
    def step(self, action):
        terminated = False
        truncated = False

        if self.n_steps==0:
            #print("AI Initial Act: ", action)
            asyncio.run(self.game_manager.make_ai_move(action))
            self.game_manager.bot_player._pick_starting_node()
        else:
            asyncio.run(self.game_manager.make_ai_move(action))
            asyncio.run(self.game_manager.make_bot_move())
        #plot_graph(manager.game_engine.state.nodes, manager.game_engine.state.edges, steps)
        for i in range(10):
            self.game_manager.game_engine.simulate_tick(TICK_INTERVAL_SECONDS)
        self.n_steps += 1
        reward, rew_info = self.rew_shape.calculate_rew(self.game_manager.ai_player.actions_dict[action], self.game_manager.game_engine.state)
        done = self.game_manager.game_engine.state.game_ended
        if done:
            terminated = True
        
        if self.n_steps>self.MAX_STEPS:
            terminated = True
        # Render only if explicitly called or mode is set to human
        if self.render_mode == 'human':
            self.render()

        return self._get_obs(), reward, terminated, truncated, rew_info
    
    def render(self):
        if self.render_mode == "human":
            return self._render_frame()

    def _render_frame(self):
        pass

        
    def close(self):
        if self.window is not None:
            pygame.display.quit()
            pygame.quit()