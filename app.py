import streamlit as st
import pandas as pd
import geopandas as gpd
import folium
from streamlit_folium import st_folium
import matplotlib.pyplot as plt
import matplotlib.colors as colors
import io
from cryptography.fernet import Fernet

# --- AUTHENTICATION LOGIC ---
def check_password():
    """Returns True if the user had the correct password."""
    def password_entered():
        """Checks whether a password entered by the user is correct."""
        if (
            st.session_state["username"] == st.secrets["credentials"]["username"]
            and st.session_state["password"] == st.secrets["credentials"]["password"]
        ):
            st.session_state["password_correct"] = True
            del st.session_state["password"]
            del st.session_state["username"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        # First run, show inputs for username & password.
        st.text_input("Username", on_change=None, key="username")
        st.text_input("Password", type="password", on_change=None, key="password")
        if st.button("Login"):
            password_entered()
            if not st.session_state.get("password_correct", False):
                st.error("😕 User not found or password incorrect")
                return False
            else:
                st.rerun()
        return False
    elif not st.session_state["password_correct"]:
        # Password not correct, show input + error.
        st.text_input("Username", on_change=None, key="username")
        st.text_input("Password", type="password", on_change=None, key="password")
        st.button("Login")
        st.error("😕 User not found or password incorrect")
        return False
    else:
        # Password correct.
        return True

# --- APP CONTENT WRAPPER ---
def main_app():
    st.title("🗺️ Parcel Size Variance Explorer")

    @st.cache_data
    def load_data():
        # 1. Get the key from secrets
        key = st.secrets["credentials"]["encryption_key"]
        cipher_suite = Fernet(key)
        
        # 2. Read the encrypted file
        with open("parcel_polygon_stat.dat", "rb") as f:
            org_data = f.read()
        
        all_data = cipher_suite.decrypt(org_data)
        
        bytes_io = io.BytesIO(all_data)
        gdf = gpd.read_file(bytes_io)
        
        if gdf.crs != "EPSG:4326":
            gdf = gdf.to_crs(epsg=4326)
            
        gdf = gdf.sort_values(by="variance_acres", ascending=False).reset_index(drop=True)
        return gdf

    gdf = load_data()

    # --- COLOR LOGIC ---
    vmin, vmax = gdf["variance_acres"].min(), gdf["variance_acres"].max()
    colormap = plt.get_cmap("RdYlGn") 
    norm = colors.Normalize(vmin=vmin, vmax=vmax)

    def get_color(feature):
        val = feature["properties"]["variance_acres"]
        rgba = colormap(norm(val))
        return colors.to_hex(rgba)

    if 'map_center' not in st.session_state:
        st.session_state.map_center = [gdf.geometry.centroid.y.mean(), gdf.geometry.centroid.x.mean()]
        st.session_state.map_zoom = 13

    # --- 1. THE MAP ---
    st.subheader("Interactive Map")
    m = folium.Map(location=st.session_state.map_center, zoom_start=st.session_state.map_zoom, tiles="OpenStreetMap")

    folium.GeoJson(
        gdf,
        style_function=lambda feature: {
            "fillColor": get_color(feature),
            "color": "black",
            "weight": 1,
            "fillOpacity": 0.4,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=["address", "variance_acres", "assessor_acres_clean", "ll_gisacre"],
            aliases=["Address:", "Extra Acres:", "Deeded Acres:", "Regrid Calculated Parcel Acres:"]
        )
    ).add_to(m)

    st_folium(m, width="100%", height=500, key="main_map")

    # --- 2. THE TABLE ---
    st.subheader("Property Data List")
    display_cols = ["address", "variance_acres", "variance_pct", "assessor_acres_clean", "ll_gisacre", "usedesc", "zoning", "saleprice"]

    selected_row = st.dataframe(
        gdf[display_cols],
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row"
    )

    if len(selected_row.selection.rows) > 0:
        idx = selected_row.selection.rows[0]
        selected_parcel = gdf.iloc[idx]
        st.session_state.map_center = [selected_parcel.geometry.centroid.y, selected_parcel.geometry.centroid.x]
        st.session_state.map_zoom = 18
        st.rerun()

    # --- 3. EXPORT ---
    st.divider()
    if st.button("Logout"):
        st.session_state["password_correct"] = False
        st.rerun()

# --- ENTRY POINT ---
st.set_page_config(layout="wide", page_title="Land Variance Explorer")

if check_password():
    main_app()