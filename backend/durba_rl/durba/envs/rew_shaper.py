import copy
from .state import GraphState
class RewardShaping:
    def __init__(self, n_nodes, n_edges, max_steps, reward_weights=None):
        self.n_nodes = n_nodes
        self.n_edges = n_edges
        self.MAX_STEPS = max_steps
        if reward_weights is None:
            self.reward_weights = {
                "attack": 1.0,          # positive reward for damaging enemy nodes
                "capture_enemy": 8.0,   # bonus when we capture enemy node
                "enemy_capture": -10.0, # penalty when enemy captures our node
                "enemy_control": -2,    # continuous penalty proportional to enemy-owned nodes
                "action_cost": -0.5,    # small negative cost per action
                "flow_to_enemy": 0.3,   # reward for successful juice transfer to enemy node
                "flow_from_enemy": -0.4,   # reward for successful defence against juice transfer from enemy node
                "dead_end_cost": -10,   # penalty when agent select dead end node in start
                "wl_points": 100,       # win lose points
            }

    def reset(self):
        self.prev_state = None
        self.n_steps = 1
        self.agent_gold_reserves = 0
        self.n_opp_prev_nodes = 0
        self.n_agent_prev_nodes = 0
        
    def check_winner(self, nodes):
        """Check the winner of the game at the end based on player maximum nodes owned"""
        us, bot = self.get_owned_nodes(nodes)
        if us>bot:
            return 1
        elif bot>us:
            return -1
        return 0
    
    def get_owned_nodes(self, nodes):
        nodes = nodes
        agent = 0
        bot = 0
        for node in nodes.values():
            if node.owner==1:
                agent += 1
            elif node.owner==2:
                bot += 1
        return agent, bot
    
    def get_enemy_node_juice_decrement(self, attacker_id, state: GraphState):
        """For those enemy nodes attack by player with attacker_id"""
        # Get those enemy nodes those are attackers neighbors
        nodes = state.nodes
        own_nodes  = [[], []]
        for id, node in nodes.items():
            if node.owner:
                own_nodes[node.owner-1].append(id)

        # Now check edges those are connecting attacker node to enemy node and are now flowing
        neigboring_enemy_nodes_ids = []
        edges = state.edges
        for edge_id, edge in edges.items():
            if attacker_id==1:
                if edge.source_node_id in own_nodes[0] and edge.target_node_id in own_nodes[1] and edge.flowing and edge.on:
                    neigboring_enemy_nodes_ids.append(edge.target_node_id)
            if attacker_id==2:
                if edge.source_node_id in own_nodes[1] and edge.target_node_id in own_nodes[0] and edge.flowing and edge.on:
                    neigboring_enemy_nodes_ids.append(edge.target_node_id)
        return neigboring_enemy_nodes_ids
    
    def get_neutral_node_juice_decrement(self, state: GraphState):
        """For those neutral nodes attack by agent"""
        # Get those neutral neighbors nodes
        nodes = state.nodes
        own_nodes  = [[], []]
        for id, node in nodes.items():
            if node.owner is None:
                own_nodes[1].append(id)
            elif node.owner==1:
                own_nodes[0].append(id)

        # Now check edges those are connecting our node to neutral node and are now flowing
        neigboring_neutral_nodes_ids = []
        edges = state.edges
        for edge_id, edge in edges.items():
            if edge.source_node_id in own_nodes[0] and edge.target_node_id in own_nodes[1] and edge.flowing and edge.on:
                neigboring_neutral_nodes_ids.append(edge.target_node_id)
        return neigboring_neutral_nodes_ids
    
    def get_change_in_juice_after_attack(self, attacker_id, state: GraphState):
        """Get change in juice at nodes attacked by attacker with id attacker_id"""
        # Get those enemy nodes those are our neighbors
        total_change_in_juice = 0
        neigboring_enemy_nodes_ids = self.get_enemy_node_juice_decrement(attacker_id, state)
        for enm_id in neigboring_enemy_nodes_ids:
            d_juice = self.prev_state.nodes[enm_id].juice - state.nodes[enm_id].juice
            if d_juice>0.1:
                total_change_in_juice += 1
            elif d_juice<-0.1:
                total_change_in_juice +=-1
        return total_change_in_juice
    
    def get_change_in_juice_neutral(self, state: GraphState):
        """Get change in juice at neutral nodes attacked by agent"""
        # Get those neutral nodes those are our neighbors
        total_change_in_juice = 0
        neigboring_neutral_nodes_ids = self.get_neutral_node_juice_decrement(state)
        for enm_id in neigboring_neutral_nodes_ids:
            total_change_in_juice += (self.prev_state.nodes[enm_id].juice - state.nodes[enm_id].juice)>0.05
        
        return total_change_in_juice
    
    def is_node_dead_end(self, node_id, state: GraphState):
        """Check if node is dead mean no outgoing edges"""
        edges_ids = state.nodes[node_id].attached_edge_ids
        source_ids = [state.edges[_id].source_node_id for _id in edges_ids]
        if node_id in source_ids:
            return False
        else:
            return True

    def calculate_rew(self, action, state: GraphState):
        """
        Compute reward based on the current game state.
        
        Parameters:
        state: instance of GameLogic containing current state.
        action: current action
        
        Returns:
        reward: a float reward value.
        """
        done = state.game_ended
        term = self.n_steps>self.MAX_STEPS
        reward = 0
        r_winner = 0
        winner = None
        if done or term:
            winner = self.check_winner(state.nodes)
            r_winner = winner*self.reward_weights["wl_points"]

        # Territory Reward = num_of_nodes_owned_by_us - num_nodes_owned_by_opponent
        n_agent_nodes, n_opp_nodes = self.get_owned_nodes(state.nodes)
        d_agent_nodes = n_agent_nodes - self.n_agent_prev_nodes
        d_opp_nodes = n_opp_nodes - self.n_opp_prev_nodes
        r_capture_node = self.reward_weights["capture_enemy"]*((d_agent_nodes)*(d_agent_nodes>=0))
        r_captured_by_enemy = self.reward_weights["enemy_capture"] * ((d_opp_nodes) * (d_opp_nodes>=0))
        r_own = r_capture_node + r_captured_by_enemy

        # Enemy continouous owned cost
        r_enemy_control = self.reward_weights["enemy_control"]*(n_opp_nodes/len(state.nodes))

        # Update prev_step_nodes for each player
        self.n_agent_prev_nodes = n_agent_nodes
        self.n_opp_prev_nodes = n_opp_nodes
        

        # Add cost of gold reserves use
        #gold_reward = (state.player_gold[1]-self.agent_gold_reserves)*0.02
        #self.agent_gold_reserves = state.player_gold[1]

        # Action Cost Reward
        r_act = 0
        if action[0] not in ["node_selection", "pass"]:
            r_act = self.reward_weights["action_cost"]

        # Cost Agent if dead end node selected
        r_dead_end = 0
        if action[0] == "node_selection":
            if self.is_node_dead_end(action[1], state):
                r_dead_end = self.reward_weights["dead_end_cost"]

        # Cost if dead_end node reversal done
        elif action[0] == "reverse_edge":
            _, tgt_node = action[1]
            if state.nodes[tgt_node].owner is None or state.nodes[tgt_node].owner == 2:
                r_dead_end = self.reward_weights["dead_end_cost"]

        # Proxy Rewards
        r_attack = 0
        r_defence = 0
        r_neutral = 0
        if self.prev_state:
            # Attack Enemy
            r_attack = self.reward_weights["flow_to_enemy"]*self.get_change_in_juice_after_attack(1, state)

            # Defence Reward
            r_defence = self.reward_weights["flow_from_enemy"]*self.get_change_in_juice_after_attack(2, state)

            # Attack Neutral
            r_neutral = self.reward_weights["flow_to_enemy"]*self.get_change_in_juice_neutral(state)
        

        reward = r_attack + r_neutral + r_defence + r_winner + r_own + r_dead_end + r_act + r_enemy_control
        self.prev_state = copy.deepcopy(state)
        self.n_steps += 1

        rew_info = {
            "r_attack": r_attack,
            "r_neutral": r_neutral,
            "r_defence": r_defence,
            "r_winner_loser": r_winner,
            "r_own": r_own,
            "r_dead_end": r_dead_end,
            "r_act": r_act,
            "r_enemy_control": r_enemy_control,
            "opponent_nodes": n_opp_nodes,
            "agent_nodes": n_agent_nodes
        }

        return reward, rew_info
    
class AdaptiveRewardShaping(RewardShaping):
    def __init__(self, n_nodes, n_edges, max_steps, reward_weights=None):
        self.n_nodes = n_nodes
        self.n_edges = n_edges
        self.MAX_STEPS = max_steps
        if reward_weights is None:
            self.reward_weights = {
                "attack": 1.0,          # positive reward for damaging enemy nodes
                "capture_enemy": 8.0,   # bonus when we capture enemy node
                "enemy_capture": -10.0, # penalty when enemy captures our node
                "enemy_control": -2, # continuous penalty proportional to enemy-owned nodes
                "action_cost": -0.5,   # small negative cost per action
                "flow_to_enemy": 0.3,   # reward for successful juice transfer to enemy node
                "flow_from_enemy": -0.4,   # reward for successful juice transfer from enemy node
                "dead_end_cost": -10,  # penalty when agent select dead end node in start
                "wl_points": 100,      # win lose points
            }

    def reset(self):
        self.prev_state = None
        self.n_steps = 1
        self.agent_gold_reserves = 0
        self.n_opp_prev_nodes = 0
        self.n_agent_prev_nodes = 0

    def get_node_importance(self, node_id, state: GraphState):
        node = state.nodes[node_id]
        # Check Outgoing nodes
        node_edges = node.attached_edge_ids
        n_out_edges = sum([1 if state.edges[e_id].source_node_id==node_id else 0 for e_id in node_edges])


        
    def check_winner(self, nodes):
        us, bot = self.get_owned_nodes(nodes)
        if us>bot:
            return 1
        elif bot>us:
            return -1
        return 0
    
    def get_owned_nodes(self, nodes):
        nodes = nodes
        agent = 0
        bot = 0
        for node in nodes.values():
            if node.owner==1:
                agent += 1
            elif node.owner==2:
                bot += 1
        return agent, bot
    
    def get_enemy_node_juice_decrement(self, attacker_id, state: GraphState):
        """For those enemy nodes attack by us"""
        # Get those enemy nodes those are our neighbors
        nodes = state.nodes
        own_nodes  = [[], []]
        for id, node in nodes.items():
            if node.owner:
                own_nodes[node.owner-1].append(id)

        # Now check edges those are connecting our node to enemy node and are now flowing
        neigboring_enemy_nodes_ids = []
        edges = state.edges
        for edge_id, edge in edges.items():
            if attacker_id==1:
                if edge.source_node_id in own_nodes[0] and edge.target_node_id in own_nodes[1] and edge.flowing and edge.on:
                    neigboring_enemy_nodes_ids.append(edge.target_node_id)
            if attacker_id==2:
                if edge.source_node_id in own_nodes[1] and edge.target_node_id in own_nodes[0] and edge.flowing and edge.on:
                    neigboring_enemy_nodes_ids.append(edge.target_node_id)
        return neigboring_enemy_nodes_ids
    
    def get_neutral_node_juice_decrement(self, state: GraphState):
        """For those enemy nodes attack by us"""
        # Get those enemy nodes those are our neighbors
        nodes = state.nodes
        own_nodes  = [[], []]
        for id, node in nodes.items():
            if node.owner is None:
                own_nodes[1].append(id)
            elif node.owner==1:
                own_nodes[0].append(id)

        # Now check edges those are connecting our node to neutral node and are now flowing
        neigboring_neutral_nodes_ids = []
        edges = state.edges
        for edge_id, edge in edges.items():
            if edge.source_node_id in own_nodes[0] and edge.target_node_id in own_nodes[1] and edge.flowing and edge.on:
                neigboring_neutral_nodes_ids.append(edge.target_node_id)
        return neigboring_neutral_nodes_ids
    
    def get_change_in_juice_after_attack(self, attacker_id, state: GraphState):
        """For those enemy nodes attack by us"""
        # Get those enemy nodes those are our neighbors
        total_change_in_juice = 0
        neigboring_enemy_nodes_ids = self.get_enemy_node_juice_decrement(attacker_id, state)
        for enm_id in neigboring_enemy_nodes_ids:
            d_juice = self.prev_state.nodes[enm_id].juice - state.nodes[enm_id].juice
            if d_juice>0.1:
                total_change_in_juice += 1
            elif d_juice<-0.1:
                total_change_in_juice +=-1
        return total_change_in_juice
    
    def get_change_in_juice_neutral(self, state: GraphState):
        """For those neutral nodes attack by us"""
        # Get those neutral nodes those are our neighbors
        total_change_in_juice = 0
        neigboring_neutral_nodes_ids = self.get_neutral_node_juice_decrement(state)
        for enm_id in neigboring_neutral_nodes_ids:
            total_change_in_juice += (self.prev_state.nodes[enm_id].juice - state.nodes[enm_id].juice)>0.05
        
        return total_change_in_juice
    
    def is_node_dead_end(self, node_id, state: GraphState):
        edges_ids = state.nodes[node_id].attached_edge_ids
        source_ids = [state.edges[_id].source_node_id for _id in edges_ids]
        if node_id in source_ids:
            return False
        else:
            return True

    def calculate_rew(self, action, state: GraphState):
        """
        Compute reward based on the current game state.
        
        Parameters:
        state: instance of GameLogic containing current state.
        action: current action
        
        Returns:
        reward: a float reward value.
        """
        done = state.game_ended
        term = self.n_steps>self.MAX_STEPS
        reward = 0
        r_winner = 0
        winner = None
        if done or term:
            winner = self.check_winner(state.nodes)
            r_winner = winner*self.reward_weights["wl_points"]

        # Territory Reward = num_of_nodes_owned_by_us - num_nodes_owned_by_opponent
        n_agent_nodes, n_opp_nodes = self.get_owned_nodes(state.nodes)
        d_agent_nodes = n_agent_nodes - self.n_agent_prev_nodes
        d_opp_nodes = n_opp_nodes - self.n_opp_prev_nodes
        r_capture_node = self.reward_weights["capture_enemy"]*((d_agent_nodes)*(d_agent_nodes>=0))
        r_captured_by_enemy = self.reward_weights["enemy_capture"] * ((d_opp_nodes) * (d_opp_nodes>=0))
        r_own = r_capture_node + r_captured_by_enemy

        # Enemy continouous owned cost
        r_enemy_control = self.reward_weights["enemy_control"]*(n_opp_nodes/len(state.nodes))

        # Update prev_step_nodes for each player
        self.n_agent_prev_nodes = n_agent_nodes
        self.n_opp_prev_nodes = n_opp_nodes
        

        # Add cost of gold reserves use
        #gold_reward = (state.player_gold[1]-self.agent_gold_reserves)*0.02
        #self.agent_gold_reserves = state.player_gold[1]

        # Action Cost Reward
        r_act = 0
        if action[0] not in ["node_selection", "pass"]:
            r_act = self.reward_weights["action_cost"]

        # Cost Agent if dead end node selected
        r_dead_end = 0
        if action[0] == "node_selection":
            if self.is_node_dead_end(action[1], state):
                r_dead_end = self.reward_weights["dead_end_cost"]

        # Cost if dead_end node reversal done
        elif action[0] == "reverse_edge":
            _, tgt_node = action[1]
            if state.nodes[tgt_node].owner is None or state.nodes[tgt_node].owner == 2:
                r_dead_end = self.reward_weights["dead_end_cost"]

        # Proxy Rewards
        r_attack = 0
        r_defence = 0
        r_neutral = 0
        if self.prev_state:
            # Attack Enemy
            r_attack = self.reward_weights["flow_to_enemy"]*self.get_change_in_juice_after_attack(1, state)

            # Defence Reward
            r_defence = self.reward_weights["flow_from_enemy"]*self.get_change_in_juice_after_attack(2, state)

            # Attack Neutral
            r_neutral = self.reward_weights["flow_to_enemy"]*self.get_change_in_juice_neutral(state)
        

        reward = r_attack + r_neutral + r_defence + r_winner + r_own + r_dead_end + r_act + r_enemy_control
        self.prev_state = copy.deepcopy(state)
        self.n_steps += 1

        rew_info = {
            "r_attack": r_attack,
            "r_neutral": r_neutral,
            "r_defence": r_defence,
            "r_winner_loser": r_winner,
            "r_own": r_own,
            "r_dead_end": r_dead_end,
            "r_act": r_act,
            "r_enemy_control": r_enemy_control,
            "opponent_nodes": n_opp_nodes,
            "agent_nodes": n_agent_nodes
        }

        return reward, rew_info

