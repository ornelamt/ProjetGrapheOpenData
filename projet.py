import networkx as nx
import folium
import osmnx as ox

# Charger le graphe déjà calculé
G = nx.read_gexf("graphe_velib.gexf")
print("Graphe chargé ✅")

# Récupérer le réseau cyclable depuis OpenStreetMap
print("Téléchargement du réseau cyclable OSM... (peut prendre 1-2 min)")
cycleway = ox.graph_from_place("Île-de-France, France", custom_filter='["highway"="cycleway"]')
print("Réseau cyclable téléchargé ✅")

# Créer la carte Folium centrée sur Paris (tiles corrigé)
carte = folium.Map(location=[48.8566, 2.3522], zoom_start=12,
                   tiles="CartoDB positron")

# Ajouter les pistes cyclables en bleu
edges = ox.graph_to_gdfs(cycleway, nodes=False)
for _, row in edges.iterrows():
    coords = [(point[1], point[0]) for point in row.geometry.coords]
    folium.PolyLine(coords, color="blue", weight=3, opacity=0.7).add_to(carte)

# Colorier les stations selon leur connectivité
for node in G.nodes():
    lat = float(G.nodes[node].get("lat", 0))
    lon = float(G.nodes[node].get("lon", 0))
    degre = G.degree(node)
    nom = G.nodes[node].get("nom", node)

    if degre == 0:
        couleur = "red"      # isolée
    elif degre <= 3:
        couleur = "orange"   # peu connectée
    else:
        couleur = "green"    # bien connectée

    folium.CircleMarker(
        location=[lat, lon],
        radius=6,
        color=couleur,
        fill=True,
        fill_opacity=0.8,
        popup=f"{nom} (degré: {degre})"
    ).add_to(carte)

# Sauvegarder la carte
carte.save("carte_velib.html")
print("Carte sauvegardée dans carte_velib.html ✅")  

