from stable_baselines3 import PPO
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import GCNConv, NNConv, global_mean_pool, GATConv
from torch_geometric.data import Data, Batch

class EdgeAwareGNN(nn.Module):
    def __init__(self, node_in_dim, edge_in_dim, hidden_dim, out_dim):
        super().__init__()

        # Edge MLP for first conv layer
        edge_net1 = nn.Sequential(
            nn.Linear(edge_in_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, node_in_dim * hidden_dim)
        )

        # Edge MLP for second conv layer
        edge_net2 = nn.Sequential(
            nn.Linear(edge_in_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim * out_dim)
        )

        self.conv1 = NNConv(node_in_dim, hidden_dim, edge_net1, aggr='mean')
        self.conv2 = NNConv(hidden_dim, out_dim, edge_net2, aggr='mean')

    def forward(self, x, edge_index, edge_attr, batch=None):
        # Ensure correct shapes
        if x.dim() == 3:
            x = x.squeeze(0)
        if edge_index.dim() == 3:
            edge_index = edge_index.squeeze(0)
        if edge_attr.dim() == 3:
            edge_attr = edge_attr.squeeze(0)

        x = torch.relu(self.conv1(x, edge_index, edge_attr))
        x = torch.relu(self.conv2(x, edge_index, edge_attr))

        if batch is not None:
            x = global_mean_pool(x, batch)
        else:
            x = x.mean(dim=0, keepdim=True)  # graph-level embedding

        return x

class EdgeAwareAttentionGNN(nn.Module):
    def __init__(self, node_in_dim, edge_dim, hidden_dim, out_dim, num_heads=4):
        super().__init__()
        self.gat1 = GATConv(
            in_channels=node_in_dim,
            out_channels=hidden_dim,
            heads=num_heads,
            concat=True,
            edge_dim=edge_dim
        )
        # second layer
        self.gat2 = GATConv(
            in_channels=hidden_dim * num_heads,
            out_channels=out_dim,
            heads=1,          # final layer: one head
            concat=False,
            edge_dim=edge_dim
        )

        # Final MLP / projection
        self.proj = nn.Linear(out_dim, out_dim)

    def forward(self, x, edge_index, edge_attr, batch):
        # First GAT layer + nonlinearity
        x = F.elu(self.gat1(x, edge_index, edge_attr))
        # Second GAT
        x = self.gat2(x, edge_index, edge_attr)

        # Pool to graph-level
        x = global_mean_pool(x, batch)

        # Projection
        x = self.proj(x)
        return x

class GNNFeatureExtractor(BaseFeaturesExtractor):
    def __init__(self, observation_space, node_in_dim, edge_in_dim, hidden_dim=128, out_dim=128):
        super().__init__(observation_space, features_dim=out_dim+16)
        self.gnn = EdgeAwareGNN(node_in_dim, edge_in_dim, hidden_dim, out_dim)

        # Edge MLP for second conv layer
        self.global_mlp = nn.Sequential(
            nn.Linear(3, 8),
            nn.ReLU(),
            nn.Linear(8, 16),
            nn.ReLU()
        )

    def forward(self, obs):
        # Extract tensors from dict
        x = obs["x"]              # [B, N, node_dim]
        edge_index = obs["edge_index"]  # [B, 2, E]
        edge_attr = obs["edge_attr"]    # [B, E, edge_dim]
        node_mask = obs.get("node_mask", None)
        edge_mask = obs.get("edge_mask", None)
        global_feats = obs["global"]

        batch_size = x.size(0)
        data_list = []

        for i in range(batch_size):
            x_i = x[i]
            ei_i = edge_index[i]
            ea_i = edge_attr[i]

            # Optional masking (zero-out padded elements)
            if node_mask is not None:
                x_i = x_i[node_mask[i] > 0]
            if edge_mask is not None:
                ea_i = ea_i[edge_mask[i] > 0]
                ei_i = ei_i[:, edge_mask[i] > 0]

            data = Data(
                x=x_i,
                edge_index=ei_i.to(torch.long),
                edge_attr=ea_i
            )
            data_list.append(data)

        # Merge into a single PyG batch
        batch = Batch.from_data_list(data_list)

        # Now pass through GNN
        gnn_embeds = self.gnn(batch.x, batch.edge_index, batch.edge_attr, batch.batch)

        # Global info part
        global_embed = self.global_mlp(global_feats)             # [B, hidden_dim]

        # Combine
        combined = torch.cat([gnn_embeds, global_embed], dim=-1)
        return combined

class GNNFeatureExtractor(BaseFeaturesExtractor):
    def __init__(self, observation_space, node_in_dim, edge_in_dim, attention= True, hidden_dim=64, out_dim=64):
        super().__init__(observation_space, features_dim=out_dim+16)
        if attention:
            self.gnn = EdgeAwareAttentionGNN(node_in_dim, edge_in_dim, hidden_dim, out_dim)
        else:
            self.gnn = EdgeAwareGNN(node_in_dim, edge_in_dim, hidden_dim, out_dim)

        # Edge MLP for second conv layer
        self.global_mlp = nn.Sequential(
            nn.Linear(3, 8),
            nn.ReLU(),
            nn.Linear(8, 16),
            nn.ReLU()
        )

    def forward(self, obs):
        # Extract tensors from dict
        x = obs["x"]              # [B, N, node_dim]
        edge_index = obs["edge_index"]  # [B, 2, E]
        edge_attr = obs["edge_attr"]    # [B, E, edge_dim]
        node_mask = obs.get("node_mask", None)
        edge_mask = obs.get("edge_mask", None)
        global_feats = obs["global"]

        batch_size = x.size(0)
        data_list = []

        for i in range(batch_size):
            x_i = x[i]
            ei_i = edge_index[i]
            ea_i = edge_attr[i]

            # Optional masking (zero-out padded elements)
            if node_mask is not None:
                x_i = x_i[node_mask[i] > 0]
            if edge_mask is not None:
                ea_i = ea_i[edge_mask[i] > 0]
                ei_i = ei_i[:, edge_mask[i] > 0]

            data = Data(
                x=x_i,
                edge_index=ei_i.to(torch.long),
                edge_attr=ea_i
            )
            data_list.append(data)

        # Merge into a single PyG batch
        batch = Batch.from_data_list(data_list)

        # Now pass through GNN
        gnn_embeds = self.gnn(batch.x, batch.edge_index, batch.edge_attr, batch.batch)

        # Global info part
        global_embed = self.global_mlp(global_feats)             # [B, hidden_dim]

        # Combine
        combined = torch.cat([gnn_embeds, global_embed], dim=-1)
        return combined


class GraphFeatureExtractor(BaseFeaturesExtractor):
    """
    Custom feature extractor for MultiInputPolicy (MaskablePPO).
    Processes node, edge, and global features separately with MLPs, then concatenates.
    """
    def __init__(self, observation_space, size: int, features_dim: int = 32):
        # Call parent constructor
        super().__init__(observation_space, features_dim)
        
        self.size = size

        # === Define subnets for each input ===
        node_input_dim = int(size * 5)
        edge_input_dim = int(size * (size - 1) / 2*4)
        global_input_dim = 3

        self.node_net = nn.Sequential(
            nn.Linear(node_input_dim, 128),
            nn.ReLU(),
            nn.Linear(128, 64),
            nn.ReLU()
        )

        self.edge_net = nn.Sequential(
            nn.Linear(edge_input_dim, 128),
            nn.ReLU(),
            nn.Linear(128, 64),
            nn.ReLU()
        )

        self.global_net = nn.Sequential(
            nn.Linear(global_input_dim, 32),
            nn.ReLU()
        )

        # Final integration layer
        concat_dim = 64 + 64 + 32
        self.final_net = nn.Sequential(
            nn.Linear(concat_dim, features_dim),
            nn.ReLU()
        )

    def forward(self, observations):
        node_feat = self.node_net(observations["nodes"])
        edge_feat = self.edge_net(observations["edges"])
        global_feat = self.global_net(observations["global"])

        concat = torch.cat([node_feat, edge_feat, global_feat], dim=1)
        return self.final_net(concat)
    
gnn_policy_kwargs = dict(
    features_extractor_class=GNNFeatureExtractor,
    features_extractor_kwargs=dict(node_in_dim=5, edge_in_dim=8, hidden_dim=64, out_dim=64, attention=True)
)
