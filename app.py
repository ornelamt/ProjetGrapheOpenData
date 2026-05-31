from flask import Flask, render_template, request, jsonify
import pandas as pd
import requests
import osmnx as ox
import networkx as nx
import os
from math import radians, sin, cos, sqrt, atan2
from datetime import datetime
import traceback

app = Flask(__name__)

# --- Fonction distance ---
def distance_metres(lat1, lon1, lat2, lon2):
    R = 6371000
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))

# --- Fonction géocodage Nominatim ---
def geocoder_adresse(adresse):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": adresse, "format": "json", "limit": 1, "countrycodes": "fr"}
    headers = {"User-Agent": "velib-projet-etudiant"}
    response = requests.get(url, headers=headers, params=params)
    data = response.json()
    if data:
        return float(data[0]["lat"]), float(data[0]["lon"])
    return None, None

# --- Charger les stations Vélib' en temps réel ---
def charger_velib_temps_reel():
    print("Chargement des stations Vélib' en temps réel...")
    url = "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/velib-disponibilite-en-temps-reel/records"
    stations = []
    offset = 0
    limit = 100

    while True:
        try:
            response = requests.get(url, params={"limit": limit, "offset": offset}, timeout=10)
            data = response.json()
            results = data.get("results", [])
            stations.extend(results)
            if len(stations) >= data.get("total_count", 0):
                break
            offset += limit
        except Exception as e:
            print(f"Erreur API Vélib' : {e}")
            break

    rows = []
    for s in stations:
        if s.get("is_installed") == "OUI":
            coords = s.get("coordonnees_geo", {})
            lat = coords.get("lat")
            lon = coords.get("lon")
            if lat and lon:
                rows.append({
                    "Nom station": s.get("name"),
                    "lat": float(lat),
                    "lon": float(lon),
                    "Capacité de la station": s.get("capacity", 0),
                    "Nombre total vélos disponibles": s.get("numbikesavailable", 0),
                    "Nombre bornettes libres": s.get("numdocksavailable", 0),
                    "Station en fonctionnement": "OUI"
                })

    df = pd.DataFrame(rows).dropna(subset=["lat", "lon"])
    print(f"Stations chargées en temps réel : {len(df)} ✅")
    return df

# --- Score de fiabilité ---
def score_fiabilite(row):
    taux = row["Nombre bornettes libres"] / row["Capacité de la station"] * 100
    if 20 <= taux <= 80:
        return "🟢 Fiable"
    elif taux < 20:
        return "🔴 Presque pleine"
    else:
        return "🟡 Très vide"

# --- Calcul pourcentage pistes cyclables ---
def calcul_pourcentage_cyclable(G, chemin):
    total = 0
    cyclable = 0
    for i in range(len(chemin) - 1):
        u, v = chemin[i], chemin[i+1]
        edge_data = G.get_edge_data(u, v)
        if edge_data:
            edge = edge_data.get(0, edge_data)
            longueur = edge.get("length", 0)
            highway = edge.get("highway", "")
            if isinstance(highway, list):
                highway = highway[0] if highway else ""
            total += longueur
            if highway in ["cycleway", "path", "track"]:
                cyclable += longueur
    if total == 0:
        return 0
    return round(cyclable / total * 100)

# --- Segmenter le chemin en tronçons cyclables et non cyclables ---
def segmenter_chemin(G, chemin):
    segments_cyclables = []
    segments_non_cyclables = []
    segment_actuel = [[G.nodes[chemin[0]]["x"], G.nodes[chemin[0]]["y"]]]
    est_cyclable_actuel = None

    for i in range(len(chemin) - 1):
        u, v = chemin[i], chemin[i+1]
        edge_data = G.get_edge_data(u, v)
        coord_v = [G.nodes[v]["x"], G.nodes[v]["y"]]

        est_cyclable = False
        if edge_data:
            edge = edge_data.get(0, edge_data)
            highway = edge.get("highway", "")
            if isinstance(highway, list):
                highway = highway[0]
            if highway in ["cycleway", "path", "track"]:
                est_cyclable = True

        if est_cyclable_actuel is None:
            est_cyclable_actuel = est_cyclable

        if est_cyclable == est_cyclable_actuel:
            segment_actuel.append(coord_v)
        else:
            if est_cyclable_actuel:
                segments_cyclables.append(segment_actuel)
            else:
                segments_non_cyclables.append(segment_actuel)
            segment_actuel = [segment_actuel[-1], coord_v]
            est_cyclable_actuel = est_cyclable

    if segment_actuel:
        if est_cyclable_actuel:
            segments_cyclables.append(segment_actuel)
        else:
            segments_non_cyclables.append(segment_actuel)

    return segments_cyclables, segments_non_cyclables

# --- Identifier les tronçons manquants ---
def identifier_troncons_manquants(G, chemin):
    troncons = []
    segment_actuel = []
    rues_manquantes = {}

    for i in range(len(chemin) - 1):
        u, v = chemin[i], chemin[i+1]
        edge_data = G.get_edge_data(u, v)
        if edge_data:
            edge = edge_data.get(0, edge_data)
            highway = edge.get("highway", "")
            if isinstance(highway, list):
                highway = highway[0] if highway else ""
            if highway not in ["cycleway", "path", "track"]:
                coord_u = [G.nodes[u]["x"], G.nodes[u]["y"]]
                coord_v = [G.nodes[v]["x"], G.nodes[v]["y"]]
                if not segment_actuel:
                    segment_actuel = [coord_u, coord_v]
                else:
                    segment_actuel.append(coord_v)
                nom_rue = edge.get("name", "Rue inconnue")
                if isinstance(nom_rue, list):
                    nom_rue = nom_rue[0] if nom_rue else "Rue inconnue"
                longueur = edge.get("length", 0)
                if nom_rue not in rues_manquantes:
                    rues_manquantes[nom_rue] = 0
                rues_manquantes[nom_rue] += longueur
            else:
                if segment_actuel:
                    troncons.append(segment_actuel)
                    segment_actuel = []

    if segment_actuel:
        troncons.append(segment_actuel)

    # Total formaté
    longueur_totale = sum(rues_manquantes.values())
    if longueur_totale >= 1000:
        total_formate = f"{round(longueur_totale / 1000, 2)} km"
    else:
        total_formate = f"{round(longueur_totale)} m"

    top_rues = sorted(
        [{"rue": r, "longueur": round(l)} for r, l in rues_manquantes.items()],
        key=lambda x: x["longueur"],
        reverse=True
    )[:5]

    return troncons, top_rues, total_formate

# --- Calcul chemin rapide ---
def calculer_chemin(G, noeud_depart, noeud_arrivee):
    chemin = nx.shortest_path(G, noeud_depart, noeud_arrivee, weight="length")
    longueur = nx.shortest_path_length(G, noeud_depart, noeud_arrivee, weight="length")
    coords = [[G.nodes[n]["x"], G.nodes[n]["y"]] for n in chemin]
    pct_cyclable = calcul_pourcentage_cyclable(G, chemin)
    segments_cyclables, segments_non_cyclables = segmenter_chemin(G, chemin)
    return chemin, longueur, coords, pct_cyclable, segments_cyclables, segments_non_cyclables

# --- Calcul chemin sécurisé ---
def calculer_chemin_securise(G, noeud_depart, noeud_arrivee):
    for u, v, k, data in G.edges(keys=True, data=True):
        highway = data.get("highway", "")
        if isinstance(highway, list):
            highway = highway[0]
        if highway in ["cycleway", "path", "track"]:
            data["length_secure"] = data["length"] * 0.3
        else:
            data["length_secure"] = data["length"] * 2
    chemin = nx.shortest_path(G, noeud_depart, noeud_arrivee, weight="length_secure")
    longueur = sum(
        G[chemin[i]][chemin[i+1]][0].get("length", 0)
        for i in range(len(chemin)-1)
    )
    coords = [[G.nodes[n]["x"], G.nodes[n]["y"]] for n in chemin]
    pct_cyclable = calcul_pourcentage_cyclable(G, chemin)
    segments_cyclables, segments_non_cyclables = segmenter_chemin(G, chemin)
    return chemin, longueur, coords, pct_cyclable, segments_cyclables, segments_non_cyclables

# --- Zone couverte ---
LAT_MIN, LAT_MAX = 48.75, 48.97
LON_MIN, LON_MAX = 2.10, 2.55

def dans_zone(lat, lon):
    return LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX

# --- Liste des communes ---
COMMUNES = [
    "Paris, France", "Boulogne-Billancourt, France",
    "Vincennes, France", "Saint-Denis, France",
    "Nanterre, France", "Créteil, France",
    "Montreuil, France", "Bobigny, France",
    "Versailles, France", "Colombes, France",
    "Asnières-sur-Seine, France", "Courbevoie, France",
    "Puteaux, France", "Suresnes, France",
    "Rueil-Malmaison, France"
]

# --- Charger les graphes au démarrage ---
print("Chargement du graphe cyclable...")
if os.path.exists("reseau_cyclo.graphml"):
    G_cyclo = ox.load_graphml("reseau_cyclo.graphml")
    print("Graphe cyclable chargé ✅")
else:
    print("Téléchargement du réseau cyclable... (5-10 min)")
    G_cyclo = ox.graph_from_place(
        COMMUNES,
        custom_filter='["highway"~"cycleway|path|track"]'
    )
    ox.save_graphml(G_cyclo, "reseau_cyclo.graphml")
    print("Réseau cyclable téléchargé et sauvegardé ✅")

if os.path.exists("reseau_bike.graphml"):
    print("Chargement du réseau routier...")
    G_bike = ox.load_graphml("reseau_bike.graphml")
    print("Réseau routier chargé ✅")
else:
    print("Téléchargement du réseau routier complet... (5-10 min)")
    G_bike = ox.graph_from_place(COMMUNES, network_type="bike")
    ox.save_graphml(G_bike, "reseau_bike.graphml")
    print("Réseau routier téléchargé et sauvegardé ✅")

# --- Charger les stations Vélib' en temps réel au démarrage ---
velib = charger_velib_temps_reel()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/actualiser_velib", methods=["GET"])
def actualiser_velib():
    global velib
    velib = charger_velib_temps_reel()
    return jsonify({"message": f"Données actualisées : {len(velib)} stations ✅"})

@app.route("/calculer", methods=["POST"])
def calculer():
    try:
        data = request.json
        adresse_depart = data["depart"]
        adresse_arrivee = data["arrivee"]
        mode = data.get("mode", "securise")
        tolerance = float(data.get("tolerance", 10)) / 100

        depart = geocoder_adresse(adresse_depart)
        arrivee = geocoder_adresse(adresse_arrivee)

        if not depart or not arrivee:
            return jsonify({"erreur": "Adresse introuvable. Vérifiez les adresses saisies."}), 400

        if not dans_zone(depart[0], depart[1]):
            return jsonify({"erreur": "L'adresse de départ est hors de la zone couverte (Paris + petite couronne)."}), 400
        if not dans_zone(arrivee[0], arrivee[1]):
            return jsonify({"erreur": "L'adresse d'arrivée est hors de la zone couverte (Paris + petite couronne)."}), 400

        # --- Heure de pointe ---
        heure_input = data.get("heure", None)
        if heure_input:
            heure = int(heure_input.split(":")[0])
        else:
            heure = datetime.now().hour
        if 7 <= heure <= 9:
            periode = "matin"
            conseil = "⚠️ Heure de pointe matin : les stations du centre sont souvent vides, partez tôt !"
        elif 17 <= heure <= 19:
            periode = "soir"
            conseil = "⚠️ Heure de pointe soir : les stations d'arrivée sont souvent pleines, vérifiez les alternatives !"
        else:
            periode = "normal"
            conseil = ""

        # --- Station de départ ---
        velib_dispos = velib[velib["Nombre total vélos disponibles"] > 0].copy()
        velib_dispos["dist"] = velib_dispos.apply(
            lambda row: distance_metres(depart[0], depart[1], row["lat"], row["lon"]), axis=1
        )
        top4_depart = velib_dispos.nsmallest(4, "dist")
        station_depart = top4_depart.iloc[0]
        alternatives_depart = top4_depart.iloc[1:4]

        # --- Station d'arrivée ---
        velib_bornettes = velib[velib["Nombre bornettes libres"] > 0].copy()
        velib_bornettes["dist"] = velib_bornettes.apply(
            lambda row: distance_metres(arrivee[0], arrivee[1], row["lat"], row["lon"]), axis=1
        )
        top4_arrivee = velib_bornettes.nsmallest(4, "dist")
        station_arrivee = top4_arrivee.iloc[0]
        alternatives_arrivee = top4_arrivee.iloc[1:4]

        # --- Alerte saturation ---
        capacite = station_arrivee["Capacité de la station"]
        bornettes = station_arrivee["Nombre bornettes libres"]
        taux_dispo = bornettes / capacite * 100
        alerte = bool(taux_dispo < 20)

        # --- Calcul des chemins ---
        noeud_dep_bike = ox.nearest_nodes(G_bike, station_depart["lon"], station_depart["lat"])
        noeud_arr_bike = ox.nearest_nodes(G_bike, station_arrivee["lon"], station_arrivee["lat"])

        trajet_rapide = None
        trajet_securise = None
        troncons_manquants = []
        top_rues = []
        total_manquant = "0 m"

        # Chemin rapide
        try:
            chemin_r, longueur_r, coords_r, pct_r, seg_cyc_r, seg_non_r = calculer_chemin(G_bike, noeud_dep_bike, noeud_arr_bike)
            dur_r = (longueur_r / 1000) / 15 * 60
            troncons_manquants, top_rues, total_manquant = identifier_troncons_manquants(G_bike, chemin_r)
            trajet_rapide = {
                "distance": round(longueur_r / 1000, 2),
                "temps": round(dur_r),
                "pct_cyclable": pct_r,
                "coords": coords_r,
                "segments_cyclables": seg_cyc_r,
                "segments_non_cyclables": seg_non_r,
                "connexion_depart": [[station_depart["lon"], station_depart["lat"]], coords_r[0]],
                "connexion_arrivee": [coords_r[-1], [station_arrivee["lon"], station_arrivee["lat"]]]
            }
        except nx.NetworkXNoPath:
            pass

        # Chemin sécurisé
        try:
            chemin_s, longueur_s, coords_s, pct_s, seg_cyc_s, seg_non_s = calculer_chemin_securise(G_bike, noeud_dep_bike, noeud_arr_bike)
            dur_s = (longueur_s / 1000) / 15 * 60
            trajet_securise = {
                "distance": round(longueur_s / 1000, 2),
                "temps": round(dur_s),
                "pct_cyclable": pct_s,
                "coords": coords_s,
                "segments_cyclables": seg_cyc_s,
                "segments_non_cyclables": seg_non_s,
                "connexion_depart": [[station_depart["lon"], station_depart["lat"]], coords_s[0]],
                "connexion_arrivee": [coords_s[-1], [station_arrivee["lon"], station_arrivee["lat"]]]
            }
        except nx.NetworkXNoPath:
            pass

        if not trajet_rapide and not trajet_securise:
            return jsonify({"erreur": "Pas de chemin trouvé entre ces adresses."}), 500

        # --- Distances à pied ---
        dist_pied1 = distance_metres(depart[0], depart[1], station_depart["lat"], station_depart["lon"])
        dist_pied2 = distance_metres(station_arrivee["lat"], station_arrivee["lon"], arrivee[0], arrivee[1])
        dur_pied1 = (dist_pied1 / 1000) / 5 * 60
        dur_pied2 = (dist_pied2 / 1000) / 5 * 60

        coords_pied1 = [[depart[1], depart[0]], [station_depart["lon"], station_depart["lat"]]]
        coords_pied2 = [[station_arrivee["lon"], station_arrivee["lat"]], [arrivee[1], arrivee[0]]]

        if trajet_rapide:
            trajet_rapide["temps_total"] = round(dur_pied1 + trajet_rapide["temps"] + dur_pied2)
        if trajet_securise:
            trajet_securise["temps_total"] = round(dur_pied1 + trajet_securise["temps"] + dur_pied2)

        # Info tolérance
        info_tolerance = None
        if trajet_rapide and trajet_securise:
            diff_km = trajet_securise["distance"] - trajet_rapide["distance"]
            diff_pct = round(diff_km / trajet_rapide["distance"] * 100) if trajet_rapide["distance"] > 0 else 0
            if diff_pct <= tolerance * 100:
                info_tolerance = f"✅ Trajet sécurisé trouvé dans ta tolérance ({diff_pct}% plus long)."
            else:
                info_tolerance = f"❌ Pas de trajet sécurisé dans ta tolérance de {int(tolerance*100)}% — le plus proche est {diff_pct}% plus long."
                trajet_securise = None

        return jsonify({
            "depart": {"lat": depart[0], "lon": depart[1]},
            "arrivee": {"lat": arrivee[0], "lon": arrivee[1]},
            "station_depart": {
                "nom": station_depart["Nom station"],
                "lat": float(station_depart["lat"]),
                "lon": float(station_depart["lon"]),
                "velos": int(station_depart["Nombre total vélos disponibles"]),
                "dist": round(dist_pied1),
                "fiabilite": score_fiabilite(station_depart)
            },
            "alternatives_depart": [
                {
                    "nom": row["Nom station"],
                    "lat": float(row["lat"]),
                    "lon": float(row["lon"]),
                    "velos": int(row["Nombre total vélos disponibles"]),
                    "dist": round(distance_metres(depart[0], depart[1], row["lat"], row["lon"])),
                    "fiabilite": score_fiabilite(row)
                }
                for _, row in alternatives_depart.iterrows()
            ],
            "station_arrivee": {
                "nom": station_arrivee["Nom station"],
                "lat": float(station_arrivee["lat"]),
                "lon": float(station_arrivee["lon"]),
                "bornettes": int(bornettes),
                "dist": round(dist_pied2),
                "alerte": alerte,
                "taux_dispo": round(taux_dispo),
                "fiabilite": score_fiabilite(station_arrivee)
            },
            "alternatives_arrivee": [
                {
                    "nom": row["Nom station"],
                    "lat": float(row["lat"]),
                    "lon": float(row["lon"]),
                    "bornettes": int(row["Nombre bornettes libres"]),
                    "dist": round(distance_metres(arrivee[0], arrivee[1], row["lat"], row["lon"])),
                    "fiabilite": score_fiabilite(row)
                }
                for _, row in alternatives_arrivee.iterrows()
            ],
            "trajet_rapide": trajet_rapide,
            "trajet_securise": trajet_securise,
            "trajet_pied1": {"coords": coords_pied1},
            "trajet_pied2": {"coords": coords_pied2},
            "troncons_manquants": troncons_manquants,
            "top_rues": top_rues,
            "total_manquant": total_manquant,
            "info_tolerance": info_tolerance,
            "heure_pointe": {
                "periode": periode,
                "conseil": conseil
            }
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"erreur": f"Erreur inattendue : {str(e)}"}), 500

if __name__ == "__main__":
    app.run(debug=True)