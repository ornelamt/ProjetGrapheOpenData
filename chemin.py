import pandas as pd
import folium
import os
import requests
from math import radians, sin, cos, sqrt, atan2

# --- Clé API OpenRouteService ---
ORS_API_KEY = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImVlNWRkNmNmNjY3ODQ2N2NiOTZiOWI3NzE1ZGVlMTBmIiwiaCI6Im11cm11cjY0In0="

# --- Fonction distance ---
def distance_metres(lat1, lon1, lat2, lon2):
    R = 6371000
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))

# --- Fonction itinéraire via OpenRouteService ---
def calculer_itineraire(lat1, lon1, lat2, lon2):
    url = "https://api.openrouteservice.org/v2/directions/cycling-regular/geojson"
    headers = {
        "Authorization": ORS_API_KEY,
        "Content-Type": "application/json"
    }
    body = {
        "coordinates": [[lon1, lat1], [lon2, lat2]]
    }
    response = requests.post(url, json=body, headers=headers)
    if response.status_code == 200:
        data = response.json()
        coords = data["features"][0]["geometry"]["coordinates"]
        distance = data["features"][0]["properties"]["summary"]["distance"]
        return coords, distance
    else:
        print(f"Erreur API : {response.status_code} - {response.text}")
        return None, None

# --- Fonction géocodage via OpenRouteService ---
def geocoder_adresse(adresse):
    url = "https://api.openrouteservice.org/geocode/search"
    headers = {"Authorization": ORS_API_KEY}
    params = {"text": adresse, "boundary.country": "FR", "size": 1}
    response = requests.get(url, headers=headers, params=params)
    data = response.json()
    coords = data["features"][0]["geometry"]["coordinates"]
    return coords[1], coords[0]  # lat, lon

# --- Charger les stations Vélib' ---
print("Chargement des stations Vélib'...")
velib = pd.read_csv("velib-disponibilite-en-temps-reel.csv", sep=";")
velib[["lat", "lon"]] = velib["Coordonnées géographiques"].str.split(",", expand=True).astype(float)
velib = velib[velib["Station en fonctionnement"] == "OUI"]
velib = velib.dropna(subset=["lat", "lon"])
print(f"Stations chargées : {len(velib)}")

# --- Adresses de départ et arrivée ---
adresse_depart = input("Entrez votre adresse de départ : ")
adresse_arrivee = input("Entrez votre adresse de travail : ")

print("Recherche des coordonnées...")
depart = geocoder_adresse(adresse_depart)
arrivee = geocoder_adresse(adresse_arrivee)
print(f"Départ : {depart}")
print(f"Arrivée : {arrivee}")

# --- Trouver la station Vélib' de départ ---
velib_dispos = velib[velib["Nombre total vélos disponibles"] > 0].copy()
velib_dispos["dist_domicile"] = velib_dispos.apply(
    lambda row: distance_metres(depart[0], depart[1], row["lat"], row["lon"]), axis=1
)
station_depart = velib_dispos.loc[velib_dispos["dist_domicile"].idxmin()]
print(f"\nStation de départ : {station_depart['Nom station']}")
print(f"Distance domicile : {station_depart['dist_domicile']:.0f}m")
print(f"Vélos disponibles : {station_depart['Nombre total vélos disponibles']}")

# --- Trouver la station Vélib' d'arrivée ---
velib_bornettes = velib[velib["Nombre bornettes libres"] > 0].copy()
velib_bornettes["dist_arrivee"] = velib_bornettes.apply(
    lambda row: distance_metres(arrivee[0], arrivee[1], row["lat"], row["lon"]), axis=1
)
station_arrivee = velib_bornettes.loc[velib_bornettes["dist_arrivee"].idxmin()]
print(f"Station d'arrivée : {station_arrivee['Nom station']}")
print(f"Bornettes libres : {station_arrivee['Nombre bornettes libres']}")

# --- Calcul des itinéraires via ORS ---
print("\nCalcul des itinéraires...")

# Itinéraire à pied domicile → station départ
coords_pied1, dist_pied1 = calculer_itineraire(
    depart[0], depart[1],
    station_depart["lat"], station_depart["lon"]
)

# Itinéraire vélo station départ → station arrivée
coords_velo, dist_velo = calculer_itineraire(
    station_depart["lat"], station_depart["lon"],
    station_arrivee["lat"], station_arrivee["lon"]
)

# Itinéraire à pied station arrivée → travail
coords_pied2, dist_pied2 = calculer_itineraire(
    station_arrivee["lat"], station_arrivee["lon"],
    arrivee[0], arrivee[1]
)

print(f"À pied domicile → station : {dist_pied1:.0f}m")
print(f"Trajet vélo : {dist_velo/1000:.2f}km")
print(f"À pied station → travail : {dist_pied2:.0f}m")

# --- Carte Folium ---
print("\nCréation de la carte...")
carte = folium.Map(location=[depart[0], depart[1]], zoom_start=13,
                   tiles="CartoDB positron")

# Trajet à pied domicile → station départ
if coords_pied1:
    folium.PolyLine(
        [(c[1], c[0]) for c in coords_pied1],
        color="gray", weight=3, opacity=0.7, dash_array="10",
        tooltip="À pied").add_to(carte)

# Trajet vélo
if coords_velo:
    folium.PolyLine(
        [(c[1], c[0]) for c in coords_velo],
        color="blue", weight=4, opacity=0.8,
        tooltip="Trajet vélo").add_to(carte)

# Trajet à pied station arrivée → travail
if coords_pied2:
    folium.PolyLine(
        [(c[1], c[0]) for c in coords_pied2],
        color="gray", weight=3, opacity=0.7, dash_array="10",
        tooltip="À pied").add_to(carte)

# Marqueurs
folium.Marker(location=[depart[0], depart[1]],
              popup="Domicile",
              icon=folium.Icon(color="red", icon="home")).add_to(carte)

folium.Marker(location=[station_depart["lat"], station_depart["lon"]],
              popup=f"Station départ : {station_depart['Nom station']}",
              icon=folium.Icon(color="green", icon="bicycle")).add_to(carte)

folium.Marker(location=[station_arrivee["lat"], station_arrivee["lon"]],
              popup=f"Station arrivée : {station_arrivee['Nom station']}",
              icon=folium.Icon(color="orange", icon="flag")).add_to(carte)

folium.Marker(location=[arrivee[0], arrivee[1]],
              popup="Travail",
              icon=folium.Icon(color="purple", icon="briefcase")).add_to(carte)

carte.save("chemin_velib.html")
print("Carte sauvegardée dans chemin_velib.html ✅")
print(f"\nRésumé :")
print(f"  Domicile → {station_depart['Nom station']} : {dist_pied1:.0f}m à pied")
print(f"  Trajet vélo : {dist_velo/1000:.2f} km")
print(f"  {station_arrivee['Nom station']} → Travail : {dist_pied2:.0f}m à pied")