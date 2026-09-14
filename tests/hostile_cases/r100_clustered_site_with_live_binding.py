def read_graph(graph_xml, G):
    graph_start = graph_xml.get("start")
    if graph_start is not None:
        G["start"] = graph_start
    return G
def read_node(node_xml, data):
    node_pid = node_xml.get("pid")
    if node_pid is not None:
        data["pid"] = node_pid
    return data
def read_edge(edge_element, data, G):
    edge_id = edge_element.get("id")
    if edge_id is not None:
        data["id"] = edge_id
    key = data.pop("networkx_key", None)
    if key is not None:
        edge_id = key
    G["edge"] = ("a", "b", edge_id)
    return G
if __name__ == "__main__":
    print(read_graph({"start": 1}, {}), read_node({"pid": 2}, {}))
    print(read_edge({"id": "e1"}, {}, {}), read_edge({}, {"networkx_key": 7}, {}), read_edge({}, {}, {}))
