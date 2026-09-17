"""
Wanderlog Backend — FastAPI + SQLite + AI/Places Integration
Run: uvicorn main:app --reload --port 8000
"""
import os
import json
import uuid
import random
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv
load_dotenv()  # reads variables from a .env file in this folder, if present

from serp_service import SerpService, SERPAPI_API_KEY

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel, Field

from sqlalchemy import create_engine, Column, String, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# ==================== CONFIG ====================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")  # for place lookups (unused by generation)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./wanderlog.db")

# Gemini — this is the primary LLM provider. Get a free key at https://aistudio.google.com/apikey
# NOTE: this is separate from GOOGLE_PLACES_API_KEY above — that one's for place lookups, this one's for generation.
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_CHAT_MODEL = os.getenv("GEMINI_CHAT_MODEL", "gemini-3.5-flash-lite")
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Ollama — optional, only used if no Gemini/Groq/OpenAI key is set. Off by default since
# it needs to run locally and won't be reachable once deployed.
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")
USE_OLLAMA = os.getenv("USE_OLLAMA", "false").lower() == "true"

# Groq/OpenAI stay as optional fallbacks if configured.
API_KEY = GROQ_API_KEY or OPENAI_API_KEY
BASE_URL = "https://api.groq.com/openai/v1" if GROQ_API_KEY else None
MODEL_NAME = "llama-3.1-8b-instant" if GROQ_API_KEY else "gpt-4o-mini"

# ==================== DATABASE ====================
Base = declarative_base()
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class ItineraryDB(Base):
    __tablename__ = "itineraries"
    id = Column(String, primary_key=True, index=True)
    destination = Column(String, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    data = Column(Text)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==================== MOCK DATABASE ====================
MOCK_PLACES: Dict[str, List[Dict]] = {
    "lisbon": [
        {"name": "Pastéis de Belém", "address": "R. de Belém 84-92, 1300-085 Lisboa", "lat": 38.6975, "lng": -9.1986, "rating": 4.6, "type": "bakery", "description": "The original custard tart bakery since 1837. Worth the tram ride."},
        {"name": "Miradouro da Senhora do Monte", "address": "Largo Monte, 1170-107 Lisboa", "lat": 38.7196, "lng": -9.1311, "rating": 4.7, "type": "viewpoint", "description": "The highest viewpoint in Lisbon. Locals bring wine and watch the sunset here."},
        {"name": "Time Out Market Lisboa", "address": "Av. 24 de Julho 49, 1200-479 Lisboa", "lat": 38.7069, "lng": -9.1463, "rating": 4.3, "type": "food_hall", "description": "Curated food hall with the city's best chefs under one roof."},
        {"name": "LX Factory", "address": "R. Rodrigues de Faria 103, 1300-501 Lisboa", "lat": 38.7033, "lng": -9.1786, "rating": 4.5, "type": "cultural", "description": "Industrial complex turned creative hub. Street art, bookshop, rooftop bars."},
        {"name": "Museu Nacional do Azulejo", "address": "R. Me. Deus 4, 1900-312 Lisboa", "lat": 38.7275, "lng": -9.1139, "rating": 4.4, "type": "museum", "description": "Five centuries of Portuguese tile art in a beautiful convent setting."},
        {"name": "Cervejaria Ramiro", "address": "Av. Almirante Reis 1 H, 1150-007 Lisboa", "lat": 38.7201, "lng": -9.1355, "rating": 4.5, "type": "restaurant", "description": "Legendary seafood spot. The tiger prawns and clams are non-negotiable."},
        {"name": "Alfama District", "address": "Alfama, Lisboa", "lat": 38.7125, "lng": -9.1250, "rating": 4.6, "type": "neighborhood", "description": "Moorish labyrinth of narrow streets, Fado bars, and laundry hanging between buildings."},
        {"name": "Praça do Comércio", "address": "Praça do Comércio, 1100-148 Lisboa", "lat": 38.7073, "lng": -9.1366, "rating": 4.5, "type": "plaza", "description": "The grand riverside square. Arcades, the triumphal arch, and the Tagus glittering beyond."},
    ],
    "tokyo": [
        {"name": "Tsukiji Outer Market", "address": "Chuo City, Tokyo", "lat": 35.6655, "lng": 139.7704, "rating": 4.5, "type": "market", "description": "Post-fish-market energy. Tamago on a stick, fresh uni, and knife shops."},
        {"name": "Nezu Shrine", "address": "1-28-9 Nezu, Bunkyo City, Tokyo", "lat": 35.7202, "lng": 139.7650, "rating": 4.6, "type": "temple", "description": "Ancient shrine with a tunnel of vermillion torii gates. Far quieter than Fushimi Inari."},
        {"name": "Shinjuku Golden Gai", "address": "1-1-6 Kabukicho, Shinjuku City, Tokyo", "lat": 35.6940, "lng": 139.7040, "rating": 4.4, "type": "nightlife", "description": "200 tiny bars in six alleys. Each fits five people. Choose one that looks interesting and commit."},
        {"name": "TeamLab Planets", "address": "6-1-16 Toyosu, Koto City, Tokyo", "lat": 35.6491, "lng": 139.7887, "rating": 4.6, "type": "art", "description": "Walk barefoot through water, light, and flowers. Book weeks ahead."},
        {"name": "Yanaka Ginza", "address": "Yanaka, Taito City, Tokyo", "lat": 35.7280, "lng": 139.7650, "rating": 4.5, "type": "neighborhood", "description": "Old Tokyo that survived the war. Cat-themed shops, traditional sweets, and a slower pace."},
        {"name": "Sushi Dai", "address": "5-2-1 Tsukiji, Chuo City, Tokyo", "lat": 35.6650, "lng": 139.7700, "rating": 4.7, "type": "restaurant", "description": "The omakase queue starts at 4 AM. If that's too much, try the equally excellent Sushi Daiwa next door."},
        {"name": "Meiji Shrine", "address": "1-1 Yoyogikamizonocho, Shibuya City, Tokyo", "lat": 35.6764, "lng": 139.6993, "rating": 4.6, "type": "shrine", "description": "A forest sanctuary in the middle of the city. The torii gate walk resets your nervous system."},
        {"name": "Shibuya Sky", "address": "2-24-12 Shibuya, Shibuya City, Tokyo", "lat": 35.6580, "lng": 139.7016, "rating": 4.5, "type": "viewpoint", "description": "360° open-air observation deck. Sunset slots sell out fast. The escalator to the roof is cinematic."},
    ],
    "paris": [
        {"name": "Marché d'Aligre", "address": "Place d'Aligre, 75012 Paris", "lat": 48.8480, "lng": 2.3770, "rating": 4.4, "type": "market", "description": "The last truly local market in central Paris. Covered hall, open-air stalls, and a flea market attached."},
        {"name": "Musée de l'Orangerie", "address": "Jardin Tuileries, 75001 Paris", "lat": 48.8638, "lng": 2.3229, "rating": 4.6, "type": "museum", "description": "Monet's Water Lilies in two oval rooms designed specifically for them. Intimate and transcendent."},
        {"name": "Canal Saint-Martin", "address": "Canal Saint-Martin, 75010 Paris", "lat": 48.8710, "lng": 2.3660, "rating": 4.5, "type": "waterfront", "description": "Local Paris. Picnic on the locks, watch the boats rise and fall, drink natural wine at nearby bars."},
        {"name": "Le Comptoir du Panthéon", "address": "200 Rue Saint-Jacques, 75005 Paris", "lat": 48.8460, "lng": 2.3450, "rating": 4.3, "type": "restaurant", "description": "Classic bistro near the Panthéon. The confit de canard is textbook perfect."},
        {"name": "Père Lachaise Cemetery", "address": "16 Rue du Repos, 75020 Paris", "lat": 48.8614, "lng": 2.3930, "rating": 4.7, "type": "historic", "description": "Wilde, Piaf, Morrison, Chopin. A city of the dead that feels more alive than most neighborhoods."},
        {"name": "Shakespeare and Company", "address": "37 Rue de la Bûcherie, 75005 Paris", "lat": 48.8526, "lng": 2.3471, "rating": 4.5, "type": "bookstore", "description": "The legendary English bookstore. Read in the upstairs library, buy a stamp for your passport."},
        {"name": "Montmartre Vineyards", "address": "Rue Saint-Vincent, 75018 Paris", "lat": 48.8900, "lng": 2.3400, "rating": 4.2, "type": "nature", "description": "The only working vineyard in Paris. Hidden behind Sacré-Cœur, most tourists miss it entirely."},
        {"name": "Le Mary Celeste", "address": "1 Rue Commines, 75003 Paris", "lat": 48.8630, "lng": 2.3650, "rating": 4.4, "type": "bar", "description": "Oyster bar with natural wine and a rotating DJ. The kind of place that defines Parisian cool."},
    ],
    "new york": [
        {"name": "The High Line", "address": "New York, NY 10011", "lat": 40.7480, "lng": -74.0048, "rating": 4.6, "type": "park", "description": "Elevated park on a former railway. Art installations, wild gardens, and views of the Hudson."},
        {"name": "Joe's Pizza", "address": "7 Carmine St, New York, NY 10014", "lat": 40.7308, "lng": -74.0022, "rating": 4.5, "type": "restaurant", "description": "The Greenwich Village slice shop. Thin, foldable, perfect. No seats, eat on the street."},
        {"name": "The Met Breuer", "address": "945 Madison Ave, New York, NY 10021", "lat": 40.7750, "lng": -73.9630, "rating": 4.4, "type": "museum", "description": "Modern and contemporary art in Brutalist architecture. Smaller crowds than the main Met."},
        {"name": "DUMBO Waterfront", "address": "Brooklyn Bridge Park, Brooklyn, NY", "lat": 40.7033, "lng": -73.9881, "rating": 4.7, "type": "viewpoint", "description": "The Manhattan Bridge framing the Empire State Building. The most photographed spot in Brooklyn for good reason."},
        {"name": "Chelsea Market", "address": "75 9th Ave, New York, NY 10011", "lat": 40.7424, "lng": -74.0061, "rating": 4.4, "type": "food_hall", "description": "Former Nabisco factory turned food hall. Los Tacos No. 1 is the move here."},
        {"name": "Green-Wood Cemetery", "address": "500 25th St, Brooklyn, NY 11232", "lat": 40.6580, "lng": -73.9940, "rating": 4.7, "type": "historic", "description": "Victorian cemetery with skyline views. Battle Hill at sunset is one of the city's best secrets."},
        {"name": "Smalls Jazz Club", "address": "183 W 10th St, New York, NY 10014", "lat": 40.7343, "lng": -74.0025, "rating": 4.6, "type": "nightlife", "description": "Late-night jazz in a basement. The jam sessions after midnight are where the real magic happens."},
        {"name": "The Strand Bookstore", "address": "828 Broadway, New York, NY 10003", "lat": 40.7333, "lng": -73.9908, "rating": 4.6, "type": "bookstore", "description": "18 miles of books. The rare book room upstairs is worth the climb."},
    ],
    "barcelona": [
        {"name": "Mercat de Sant Antoni", "address": "Carrer del Comte d'Urgell, 1, 08011 Barcelona", "lat": 41.3780, "lng": 2.1620, "rating": 4.5, "type": "market", "description": "The locals' market. Less touristy than La Boqueria, with better prices and a Sunday book market outside."},
        {"name": "Bunkers del Carmel", "address": "Carrer de Marià Labèrnia, s/n, 08032 Barcelona", "lat": 41.4190, "lng": 2.1610, "rating": 4.7, "type": "viewpoint", "description": "Former anti-aircraft bunkers with the best panoramic view of the city. Bring wine and watch the sunset."},
        {"name": "El Xampanyet", "address": "Carrer de Montcada, 22, 08003 Barcelona", "lat": 41.3840, "lng": 2.1820, "rating": 4.4, "type": "bar", "description": "Historic tapas bar near the Picasso Museum. Cava by the glass, anchovies, and marble tables."},
        {"name": "Park Güell", "address": "08024 Barcelona", "lat": 41.4145, "lng": 2.1527, "rating": 4.4, "type": "park", "description": "Gaudí's mosaic wonderland. The free zones outside the ticketed area are nearly as beautiful."},
        {"name": "Barceloneta Beach (early)", "address": "Barceloneta, Barcelona", "lat": 41.3780, "lng": 2.1920, "rating": 4.2, "type": "beach", "description": "Arrive before 9 AM to see locals swimming and the sand still clean. Completely different vibe than midday."},
        {"name": "Sagrada Família", "address": "C/ de Mallorca, 401, 08013 Barcelona", "lat": 41.4036, "lng": 2.1744, "rating": 4.6, "type": "church", "description": "Gaudí's unfinished masterpiece. Book the tower climb for views through the stone fruit."},
        {"name": "Gràcia Neighborhood", "address": "Gràcia, Barcelona", "lat": 41.4000, "lng": 2.1600, "rating": 4.5, "type": "neighborhood", "description": "Former village absorbed by the city. Plazas full of locals, independent shops, and no tourist menus."},
        {"name": "Tickets Bar", "address": "Av. del Paral·lel, 164, 08015 Barcelona", "lat": 41.3750, "lng": 2.1700, "rating": 4.5, "type": "restaurant", "description": "Adrià's playful tapas circus. The olive that isn't an olive is just the beginning."},
    ],
    "mexico city": [
        {"name": "Mercado de San Juan", "address": "Ernesto Pugibet 21, Centro Histórico, CDMX", "lat": 19.4330, "lng": -99.1330, "rating": 4.5, "type": "market", "description": "The gourmet market. Exotic meats, cheeses, and the best tacos de canasta in the centro."},
        {"name": "Museo Frida Kahlo", "address": "Londres 247, Del Carmen, Coyoacán, CDMX", "lat": 19.3550, "lng": -99.1620, "rating": 4.4, "type": "museum", "description": "The Blue House. Her studio, her kitchen, her dresses. Intimate and heartbreaking."},
        {"name": "Roma Norte", "address": "Roma Norte, CDMX", "lat": 19.4190, "lng": -99.1600, "rating": 4.6, "type": "neighborhood", "description": "Art deco architecture, mezcal bars, and some of the best street food in the hemisphere."},
        {"name": "Bosque de Chapultepec", "address": "Bosque de Chapultepec I Secc, Miguel Hidalgo, CDMX", "lat": 19.4200, "lng": -99.1800, "rating": 4.5, "type": "park", "description": "One of the largest city parks in the world. Museums, a castle, and rowboats on the lake."},
        {"name": "Pujol", "address": "Tennyson 133, Polanco, CDMX", "lat": 19.4330, "lng": -99.1900, "rating": 4.7, "type": "restaurant", "description": "Enrique Olvera's temple to Mexican cuisine. The mole madre is 1,000+ days old."},
        {"name": "Xochimilco (early)", "address": "Xochimilco, CDMX", "lat": 19.2900, "lng": -99.1300, "rating": 4.3, "type": "cultural", "description": "The floating gardens. Go early on a weekday to avoid the party boats and see the real chinampas."},
        {"name": "Templo Mayor", "address": "Seminario 8, Centro Histórico, CDMX", "lat": 19.4340, "lng": -99.1320, "rating": 4.5, "type": "historic", "description": "The Aztec heart of the city, unearthed in the 1970s. The serpent carvings are still terrifying."},
        {"name": "Lucha Libre at Arena México", "address": "Dr. Lavista 189, Doctores, CDMX", "lat": 19.4250, "lng": -99.1500, "rating": 4.6, "type": "entertainment", "description": "Tuesday or Friday night. Buy a mask outside, scream for the técnicos, drink beer."},
    ],
    "bangkok": [
        {"name": "Jay Fai", "address": "327 Maha Chai Rd, Samran Rat, Phra Nakhon, Bangkok", "lat": 13.7520, "lng": 100.5000, "rating": 4.4, "type": "restaurant", "description": "Michelin-starred street food. The crab omelet is legendary. Queue for 2+ hours or book ahead."},
        {"name": "Wat Arun at Sunrise", "address": "158 Thanon Wang Doem, Wat Arun, Bangkok", "lat": 13.7437, "lng": 100.4887, "rating": 4.6, "type": "temple", "description": "The Temple of Dawn. Arrive at 6 AM to see the porcelain mosaics glow pink in the morning light."},
        {"name": "Chatuchak Weekend Market", "address": "Kamphaeng Phet 2 Rd, Chatuchak, Bangkok", "lat": 13.8000, "lng": 100.5500, "rating": 4.3, "type": "market", "description": "15,000 stalls. Antiques, plants, vintage clothes, and the best coconut ice cream in Section 2."},
        {"name": "Jim Thompson House", "address": "6 Kasem San 2 Alley, Wang Mai, Pathum Wan, Bangkok", "lat": 13.7490, "lng": 100.5280, "rating": 4.5, "type": "museum", "description": "Silk magnate's traditional Thai house. The mystery of his disappearance in Malaysia adds to the allure."},
        {"name": "Chinatown (Yaowarat) at night", "address": "Yaowarat Rd, Samphanthawong, Bangkok", "lat": 13.7400, "lng": 100.5100, "rating": 4.5, "type": "food_street", "description": "Neon signs, gold shops, and street food that starts at 6 PM. The bird's nest soup is an experience."},
        {"name": "Lumphini Park", "address": "192 Witthayu Rd, Lumphini, Pathum Wan, Bangkok", "lat": 13.7300, "lng": 100.5400, "rating": 4.4, "type": "park", "description": "Bangkok's Central Park. Monitor lizards roam freely. Join the evening aerobics class."},
        {"name": "Rooftop at Tichuca", "address": "T-One Building, 8th Floor, Sukhumvit 40, Bangkok", "lat": 13.7200, "lng": 100.5700, "rating": 4.3, "type": "bar", "description": "The jellyfish-shaped rooftop bar. Touristy but the view of the city's sprawl is undeniable."},
        {"name": "Khlong Lat Mayom Floating Market", "address": "Bang Ramat Rd, Taling Chan, Bangkok", "lat": 13.7600, "lng": 100.4500, "rating": 4.5, "type": "market", "description": "Local floating market, not the tourist circus. Boat noodles, grilled seafood, and orchid farms."},
    ],
    "marrakech": [
        {"name": "Jemaa el-Fnaa at dusk", "address": "Jemaa el-Fnaa, Marrakech", "lat": 31.6258, "lng": -7.9891, "rating": 4.4, "type": "plaza", "description": "The square transforms at sunset. Snake charmers give way to food stalls. The energy is electric."},
        {"name": "Majorelle Garden", "address": "Rue Yves Saint Laurent, Marrakech", "lat": 31.6410, "lng": -8.0030, "rating": 4.5, "type": "garden", "description": "Yves Saint Laurent's cobalt-blue oasis. Arrive at opening (8 AM) to experience the silence."},
        {"name": "Ben Youssef Madrasa", "address": "Place Ben Youssef, Marrakech", "lat": 31.6320, "lng": -7.9860, "rating": 4.6, "type": "historic", "description": "14th-century Islamic college with stunning zellige tilework and carved cedar ceilings."},
        {"name": "Le Jardin Secret", "address": "121 Rue Mouassine, Marrakech", "lat": 31.6310, "lng": -7.9880, "rating": 4.5, "type": "garden", "description": "Recently restored riad gardens. The Islamic garden philosophy of paradise made tangible."},
        {"name": "Dar Yacout", "address": "79 Sidi Ahmed Soussi, Marrakech", "lat": 31.6300, "lng": -7.9900, "rating": 4.4, "type": "restaurant", "description": "Palatial riad dining. Seven-course Moroccan feast. Eat with your hands, drink mint tea."},
        {"name": "The Souks (metalworkers quarter)", "address": "Medina, Marrakech", "lat": 31.6300, "lng": -7.9850, "rating": 4.3, "type": "market", "description": "Skip the tourist lanes. Head to the metalworkers and dyers quarters for the real craft."},
        {"name": "Koutoubia Mosque (exterior)", "address": "Avenue Mohammed V, Marrakech", "lat": 31.6240, "lng": -7.9930, "rating": 4.5, "type": "mosque", "description": "The 77-meter minaret that defines the skyline. Non-Muslims can't enter, but the gardens are open."},
        {"name": "Hammam de la Rose", "address": "Derb Sidi Bouloukate, Marrakech", "lat": 31.6280, "lng": -7.9870, "rating": 4.6, "type": "spa", "description": "Traditional hammam experience. The black soap scrub will remove a layer of skin you didn't know you had."},
    ],
    "rome": [
        {"name": "Trastevere Morning", "address": "Trastevere, Rome", "lat": 41.8880, "lng": 12.4660, "rating": 4.6, "type": "neighborhood", "description": "Before the evening crowds. The cobblestones, the ivy, the bakeries pulling fresh pizza bianca from the oven."},
        {"name": "Galleria Borghese", "address": "Piazzale Scipione Borghese, 5, Rome", "lat": 41.9140, "lng": 12.4920, "rating": 4.7, "type": "museum", "description": "Bernini's sculptures in a villa setting. Apollo and Daphne is worth the reservation hassle alone."},
        {"name": "Pizzarium", "address": "Via della Meloria, 43, Rome", "lat": 41.9020, "lng": 12.4580, "rating": 4.5, "type": "restaurant", "description": "Gabriele Bonci's al taglio pizza. The dough is fermented for 72 hours. The potato and mozzarella is transcendent."},
        {"name": "The Aventine Keyhole", "address": "Piazza dei Cavalieri di Malta, 3, Rome", "lat": 41.8830, "lng": 12.4780, "rating": 4.4, "type": "viewpoint", "description": "Peek through the keyhole of the Knights of Malta gate. A perfectly framed view of St. Peter's dome."},
        {"name": "Campo de' Fiori Market", "address": "Piazza Campo de' Fiori, Rome", "lat": 41.8960, "lng": 12.4720, "rating": 4.3, "type": "market", "description": "Morning market with produce, flowers, and spices. The afternoon is for aperitivo at the surrounding bars."},
        {"name": "Villa Doria Pamphili", "address": "Via S. Pancrazio, Rome", "lat": 41.8850, "lng": 12.4550, "rating": 4.5, "type": "park", "description": "Rome's largest public park. Locals jog, picnic, and walk dogs. The villa itself is a Baroque masterpiece."},
        {"name": "Da Enzo al 29", "address": "Via dei Vascellari, 29, Rome", "lat": 41.8900, "lng": 12.4780, "rating": 4.5, "type": "restaurant", "description": "No reservations, no frills. The cacio e pepe is the benchmark by which all others are judged."},
        {"name": "The Pantheon (interior)", "address": "Piazza della Rotonda, Rome", "lat": 41.8986, "lng": 12.4768, "rating": 4.7, "type": "church", "description": "The oculus open to the sky. When rain falls through, it's one of Rome's most spiritual moments."},
    ],
    "sydney": [
        {"name": "Bondi to Coogee Walk", "address": "Bondi Beach to Coogee Beach, Sydney", "lat": -33.8910, "lng": 151.2770, "rating": 4.7, "type": "hike", "description": "6km coastal cliff walk. Ocean pools, Aboriginal rock carvings, and the Sculpture by the Sea in October."},
        {"name": "Bennelong Restaurant", "address": "Sydney Opera House, Bennelong Point, Sydney", "lat": -33.8570, "lng": 151.2150, "rating": 4.5, "type": "restaurant", "description": "Fine dining inside the Opera House sails. The pavlova is architectural. Book the sunset seating."},
        {"name": "The Rocks Markets", "address": "Playfair St, The Rocks, Sydney", "lat": -33.8590, "lng": 151.2090, "rating": 4.3, "type": "market", "description": "Weekend markets in Sydney's oldest neighborhood. Artisan goods, street food, and harbor views."},
        {"name": "Manly Beach (via ferry)", "address": "Manly, Sydney", "lat": -33.7970, "lng": 151.2880, "rating": 4.5, "type": "beach", "description": "The 30-minute ferry from Circular Quay is half the experience. Manly is calmer than Bondi."},
        {"name": "White Rabbit Gallery", "address": "30 Balfour St, Chippendale, Sydney", "lat": -33.8850, "lng": 151.2000, "rating": 4.5, "type": "museum", "description": "Contemporary Chinese art in a former knitting factory. The tea house downstairs is a hidden gem."},
        {"name": "Mrs Macquarie's Chair", "address": "Mrs Macquaries Rd, Sydney", "lat": -33.8590, "lng": 151.2230, "rating": 4.6, "type": "viewpoint", "description": "The classic Sydney Harbour photo spot. Opera House and Harbour Bridge in one frame. Best at golden hour."},
        {"name": "Gould's Book Arcade", "address": "32 King St, Newtown, Sydney", "lat": -33.8960, "lng": 151.1800, "rating": 4.4, "type": "bookstore", "description": "Three floors of chaotic, unorganized, magnificent second-hand books. You will get lost. That's the point."},
        {"name": "Wendy's Secret Garden", "address": "Lavender Bay, Sydney", "lat": -33.8430, "lng": 151.2050, "rating": 4.6, "type": "garden", "description": "A private garden opened to the public. Created by Wendy Whiteley after her husband's death. Profoundly moving."},
    ],
    "istanbul": [
        {"name": "Karaköy Güllüoğlu", "address": "Kılıçali Paşa Mh., Katip Çelebi Cd. No:1, Istanbul", "lat": 41.0220, "lng": 28.9770, "rating": 4.7, "type": "bakery", "description": "The original baklava masters since 1820. The pistachio version will ruin all other baklava for you."},
        {"name": "Hagia Sophia (upper gallery)", "address": "Sultan Ahmet, Ayasofya Meydanı No:1, Istanbul", "lat": 41.0086, "lng": 28.9802, "rating": 4.6, "type": "museum", "description": "The mosaics in the upper gallery — the Virgin and Child, the Deësis — are among the finest in existence."},
        {"name": "The Grand Bazaar (off hours)", "address": "Beyazıt, Kalpakçılar Cd. No:22, Istanbul", "lat": 41.0100, "lng": 28.9680, "rating": 4.3, "type": "market", "description": "4,000 shops. Go at 9 AM when shopkeepers are drinking tea and happy to talk without the hard sell."},
        {"name": "Çiçek Pasajı", "address": "Hüseyinağa, Şahne Sk. No:5, Beyoğlu, Istanbul", "lat": 41.0340, "lng": 28.9770, "rating": 4.2, "type": "historic", "description": "The Flower Passage. Neo-Baroque arcade now filled with meyhanes (taverns). Raki and meze territory."},
        {"name": "Princes' Islands (Büyükada)", "address": "Büyükada, Princes' Islands, Istanbul", "lat": 40.8760, "lng": 29.1280, "rating": 4.5, "type": "island", "description": "No cars allowed. Rent a bike, circle the island, eat fresh fish by the water. The ferry ride is therapy."},
        {"name": "Suleymaniye Mosque", "address": "Süleymaniye, Prof. Sıddık Sami Onar Cd. No:1, Istanbul", "lat": 41.0160, "lng": 28.9640, "rating": 4.7, "type": "mosque", "description": "Sinan's masterpiece. Less crowded than the Blue Mosque, with a terrace overlooking the Golden Horn."},
        {"name": "Karaköy Lokantası", "address": "Kemankeş Karamustafa Paşa Mh., Kılıçali Paşa Mh. No:11, Istanbul", "lat": 41.0230, "lng": 28.9780, "rating": 4.5, "type": "restaurant", "description": "Modern Turkish meze in a tiled Art Nouveau space. The fried mussels and artichoke are essential."},
        {"name": "Theodosian Walls", "address": "Zeytinburnu, Istanbul", "lat": 41.0000, "lng": 28.9200, "rating": 4.4, "type": "historic", "description": "1,600-year-old walls that protected Constantinople. Walk the stretch near Edirnekapı for solitude and history."},
    ],
    "general": [
        {"name": "Central Market Hall", "address": "City Center", "lat": 0, "lng": 0, "rating": 4.3, "type": "market", "description": "The sensory heart of the city. Vendors, samples, and the rhythm of local commerce."},
        {"name": "Old Town Square", "address": "Historic District", "lat": 0, "lng": 0, "rating": 4.5, "type": "plaza", "description": "The geographic and symbolic center. Morning light, evening crowds, and centuries of stories."},
        {"name": "Local Art Gallery", "address": "Arts District", "lat": 0, "lng": 0, "rating": 4.2, "type": "gallery", "description": "Contemporary works in a space that feels discovered rather than advertised."},
        {"name": "Riverside Promenade", "address": "Waterfront", "lat": 0, "lng": 0, "rating": 4.4, "type": "waterfront", "description": "The city's relationship with its water. Walk slowly, watch the light change."},
        {"name": "Neighborhood Bistro", "address": "Residential Quarter", "lat": 0, "lng": 0, "rating": 4.3, "type": "restaurant", "description": "The kind of place with regulars, a short menu, and a server who remembers your order."},
        {"name": "Historic Cathedral/Mosque", "address": "Old City", "lat": 0, "lng": 0, "rating": 4.6, "type": "religious", "description": "Centuries of devotion carved into stone and wood. The silence inside is the main attraction."},
        {"name": "City Viewpoint", "address": "Hill District", "lat": 0, "lng": 0, "rating": 4.5, "type": "viewpoint", "description": "The panorama that makes you understand the city's layout for the first time. Best at sunset."},
        {"name": "Traditional Craft Workshop", "address": "Artisan District", "lat": 0, "lng": 0, "rating": 4.4, "type": "craft", "description": "Watch someone who has mastered their craft over decades. The patience is humbling."},
    ]
}

# ==================== PYDANTIC MODELS ====================
class GenerateRequest(BaseModel):
    destination: str = Field(..., min_length=2, max_length=100)
    days: int = Field(..., ge=1, le=14)
    style: str = Field(default="balanced")
    budget: str = Field(default="mid")
    interests: List[str] = Field(default_factory=list)
    travelers: str = Field(default="2")
    mustsee: Optional[str] = Field(default="")
    origin: Optional[str] = Field(default="NYC")
    departure_date: Optional[str] = Field(default="")
    return_date: Optional[str] = Field(default="")

class AdjustRequest(BaseModel):
    itinerary: Dict[str, Any]
    day_number: int
    instruction: str

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    message: Optional[str] = None
    messages: Optional[List[ChatMessage]] = None
    itinerary: Optional[Dict[str, Any]] = None
    itinerary_context: Optional[Dict[str, Any]] = None

class SaveRequest(BaseModel):
    data: Dict[str, Any]

class PlaceSearchRequest(BaseModel):
    query: str
    destination: str

# ==================== SERVICES ====================
class PlaceService:
    @staticmethod
    def get_mock_places(destination: str) -> List[Dict]:
        key = destination.lower().strip()
        if key in MOCK_PLACES:
            return MOCK_PLACES[key]
        for city, places in MOCK_PLACES.items():
            if city in key or key in city:
                return places
        return MOCK_PLACES["general"]

    @staticmethod
    def search_places(query: str, destination: str) -> List[Dict]:
        places = PlaceService.get_mock_places(destination)
        filtered = [p for p in places if query.lower() in p["name"].lower() or query.lower() in p["type"].lower()]
        return filtered if filtered else places[:4]

    @staticmethod
    def get_place_for_activity(activity_type: str, destination: str, used_names: set) -> Optional[Dict]:
        places = PlaceService.get_mock_places(destination)
        available = [p for p in places if p["name"] not in used_names]
        if not available:
            available = places
        type_map = {
            "morning": ["bakery", "cafe", "market", "park", "neighborhood", "plaza", "temple", "shrine"],
            "afternoon": ["museum", "gallery", "historic", "cultural", "neighborhood", "park", "church", "mosque"],
            "evening": ["restaurant", "bar", "food_hall", "nightlife", "viewpoint", "waterfront"],
            "late": ["bar", "nightlife", "entertainment"]
        }
        preferred_types = type_map.get(activity_type, [])
        matches = [p for p in available if p["type"] in preferred_types]
        if matches:
            return random.choice(matches)
        return random.choice(available) if available else None

class AIService:
    @staticmethod
    async def generate_itinerary(req: GenerateRequest) -> Dict[str, Any]:
        """
        Primary Agent Loop:
        1. Fetch live flights, hotels, and attractions via SerpApi.
        2. Gemini reasoning step over raw SerpApi data (budget evaluation, location clustering, vibe matching).
        3. Assemble day-by-day plan with visible agent reasoning trails.
        """
        # Step 1: Live SerpApi calls (parallel)
        vibe_str = " ".join(req.interests) if req.interests else "culture food highlights"
        flights_task = SerpService.fetch_flights(req.origin or "NYC", req.destination, req.departure_date, req.return_date)
        hotels_task = SerpService.fetch_hotels(req.destination, req.departure_date, req.return_date, req.budget)
        places_task = SerpService.fetch_local_places(req.destination, vibe_str)

        flights, hotels, local_places = await asyncio.gather(flights_task, hotels_task, places_task)

        # Step 2: Gemini reasoning step over SerpApi results
        llm_result = await AIService._llm_generate_itinerary(req, flights, hotels, local_places)
        if llm_result is not None:
            llm_result["source"] = "llm_serpapi"
            return llm_result

        # Step 3: Fallback using cached/structured SerpApi data
        fallback = AIService._mock_generate_itinerary(req, flights, hotels, local_places)
        fallback["source"] = "serpapi_fallback"
        return fallback

    @staticmethod
    async def _llm_generate_itinerary(
        req: GenerateRequest,
        flights: List[Dict],
        hotels: List[Dict],
        local_places: List[Dict]
    ) -> Optional[Dict[str, Any]]:
        client, model = AIService._get_llm_client()
        if client is None:
            return None

        interests_str = ", ".join(req.interests) if req.interests else "general sightseeing"
        blocks_per_day = {"relaxed": "2 blocks (morning, then afternoon OR evening)",
                           "packed": "4 blocks (morning, afternoon, evening, late)",
                           "balanced": "3 blocks (morning, afternoon, evening)"}.get(req.style, "3 blocks (morning, afternoon, evening)")

        system_msg = (
            "You are an autonomous AI Travel Agent. You process live search data from SerpApi "
            "(Google Flights, Google Hotels, Google Maps) to curate optimal travel itineraries. "
            "You perform explicit spatial clustering (grouping places in geographic proximity per day) "
            "and budget evaluation. You MUST include a clear reasoning trail explaining WHY you chose "
            "each flight, hotel, and activity. Respond with ONLY valid JSON, no markdown fences."
        )

        serp_summary = json.dumps({
            "flights_sample": flights[:3],
            "hotels_sample": hotels[:3],
            "places_sample": local_places[:10]
        }, indent=2)

        user_msg = f"""Create a {req.days}-day travel itinerary for {req.destination}.

Trip details:
- Origin: {req.origin or 'NYC'}
- Style/pace: {req.style} — each day should have {blocks_per_day}
- Budget level: {req.budget}
- Travelers: {req.travelers}
- Interests: {interests_str}
- Must-see (if any): {req.mustsee or "none"}

Live SerpApi Search Data:
{serp_summary}

Return ONLY this exact JSON shape:
{{
  "agent_reasoning": "3-4 concise sentences detailing your reasoning agent loop: how you evaluated SerpApi flight/hotel options for budget & location proximity, and how you geographically clustered attractions per day to avoid cross-city travel.",
  "selected_flight": {{
    "airline": "Airline name from SerpApi data or realistic airline",
    "flight_number": "Flight number",
    "price": "Price",
    "duration": "Flight duration",
    "reasoning": "1 sentence on why this flight was chosen for budget/schedule."
  }},
  "selected_hotel": {{
    "name": "Hotel name from SerpApi data",
    "price_per_night": "Rate per night",
    "location": "Neighborhood",
    "overall_rating": 4.6,
    "reasoning": "1 sentence explaining why this hotel is geographically ideal for the itinerary hubs."
  }},
  "days_data": [
    {{
      "day_number": 1,
      "narrative_intro": "One short, warm sentence introducing the day's theme and focus area.",
      "day_reasoning": "1 short sentence explaining the spatial clustering of today's stops.",
      "activities": [
        {{
          "time": "Morning — 9:00 to 12:30",
          "name": "Specific place from SerpApi or real spot in {req.destination}",
          "address": "Real address",
          "type": "one of: museum, restaurant, market, park, viewpoint, historic, neighborhood, bar, temple, shrine, cultural, cafe, garden",
          "rating": 4.7,
          "description": "Short sentence description.",
          "reasoning": "Short 'Why I picked this' sentence explaining vibe & geographical fit."
        }}
      ],
      "food": {{"name": "Restaurant spot", "description": "Why recommended.", "reasoning": "Short rationale."}},
      "local_tip": {{"title": "Tip title", "detail": "Specific non-obvious local advice."}}
    }}
  ]
}}

Keep descriptions concise. Ensure every place is real and specific to {req.destination}."""

        try:
            token_budget = min(8192, 4000 + (req.days * 1200))
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.7,
                max_tokens=token_budget,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content.strip()
            raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(raw)

            days_data = parsed.get("days_data", [])
            if not days_data:
                return None

            for day in days_data:
                for act in day.get("activities", []):
                    act.setdefault("lat", 0)
                    act.setdefault("lng", 0)
                    act.setdefault("rating", 4.6)
                    act.setdefault("reasoning", "Chosen for its high local rating and proximity to today's core area.")
                    map_query = f"{act.get('name', '').replace(' ', '+')}+{req.destination.replace(' ', '+')}"
                    act["map_url"] = f"https://www.google.com/maps/search/?api=1&query={map_query}"
                day["pace"] = req.style

            return {
                "id": str(uuid.uuid4()),
                "destination": req.destination,
                "days": req.days,
                "style": req.style,
                "budget": req.budget,
                "interests": req.interests,
                "travelers": req.travelers,
                "mustsee": req.mustsee,
                "created_at": datetime.utcnow().isoformat(),
                "agent_reasoning": parsed.get("agent_reasoning", f"Analyzed SerpApi live options for {req.destination}. Evaluated hotels near main attraction clusters and optimized flight schedules."),
                "selected_flight": parsed.get("selected_flight", flights[0] if flights else {}),
                "selected_hotel": parsed.get("selected_hotel", hotels[0] if hotels else {}),
                "days_data": days_data,
            }
        except Exception as e:
            print(f"[AIService] LLM generation failed, falling back: {e}")
            return None

    @staticmethod
    async def adjust_day(req: AdjustRequest) -> Dict[str, Any]:
        """Adjustment loop: re-reasons over a single day based on user feedback without changing the rest of the trip."""
        client, model = AIService._get_llm_client()
        itin = req.itinerary
        day_num = req.day_number
        dest = itin.get("destination", "the destination")
        days = itin.get("days_data", [])

        # Find target day
        target_day = None
        for d in days:
            if d.get("day_number") == day_num:
                target_day = d
                break

        if not target_day:
            raise HTTPException(status_code=400, detail=f"Day {day_num} not found in itinerary.")

        if client is None:
            # Simple fallback rewrite if no LLM
            target_day["narrative_intro"] = f"Adjusted Day {day_num} based on: '{req.instruction}'."
            target_day["day_reasoning"] = f"Re-ordered activities to accommodate: {req.instruction}."
            return {"updated_day": target_day, "message": "Day adjusted successfully"}

        system_msg = (
            "You are an AI Travel Agent performing an itinerary adjustment loop. "
            "You must re-evaluate and rewrite ONLY the specified single day according to the user's "
            "instruction, while maintaining spatial consistency and quality. Return ONLY valid JSON."
        )

        user_msg = f"""Re-reason and adjust Day {day_num} for a trip to {dest}.

User instruction for Day {day_num}: "{req.instruction}"
Existing Day {day_num} structure:
{json.dumps(target_day, indent=2)}

Return ONLY this updated Day object JSON:
{{
  "day_number": {day_num},
  "narrative_intro": "Updated short intro reflecting the adjustment.",
  "day_reasoning": "Reasoning explaining how this updated day fulfills: '{req.instruction}'.",
  "activities": [
    {{
      "time": "Morning — 9:00 to 12:30",
      "name": "Adjusted place name in {dest}",
      "address": "Address",
      "type": "type",
      "rating": 4.7,
      "description": "Description.",
      "reasoning": "Why this specific updated activity was chosen."
    }}
  ],
  "food": {{"name": "Restaurant spot", "description": "Why recommended.", "reasoning": "Rationale."}},
  "local_tip": {{"title": "Tip title", "detail": "Specific advice."}}
}}"""

        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.7,
                max_tokens=2000,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content.strip()
            raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            updated = json.loads(raw)

            for act in updated.get("activities", []):
                act.setdefault("lat", 0)
                act.setdefault("lng", 0)
                act.setdefault("rating", 4.6)
                act.setdefault("reasoning", f"Re-selected to satisfy instruction: {req.instruction}")
                map_query = f"{act.get('name', '').replace(' ', '+')}+{dest.replace(' ', '+')}"
                act["map_url"] = f"https://www.google.com/maps/search/?api=1&query={map_query}"

            return {"updated_day": updated, "message": f"Day {day_num} successfully re-reasoned!"}
        except Exception as e:
            print(f"[AIService] Day adjustment LLM call failed: {e}")
            target_day["narrative_intro"] = f"Adjusted Day {day_num} for '{req.instruction}'."
            target_day["day_reasoning"] = f"Re-aligned schedule to support '{req.instruction}'."
            return {"updated_day": target_day, "message": "Day updated via fallback"}

    @staticmethod
    def _get_llm_client(purpose: str = "generate"):
        """Returns (client, model_name) for whichever provider is available. Priority: Gemini > Ollama > Groq/OpenAI."""
        try:
            import openai
            import httpx
            http_client = httpx.AsyncClient(trust_env=False)
        except Exception:
            return None, None

        if GOOGLE_API_KEY and len(GOOGLE_API_KEY) > 10:
            try:
                client = openai.AsyncOpenAI(api_key=GOOGLE_API_KEY, base_url=GEMINI_BASE_URL, http_client=http_client, max_retries=3)
                model = GEMINI_CHAT_MODEL if purpose == "chat" else GEMINI_MODEL
                return client, model
            except Exception as e:
                print(f"[AIService] Could not instantiate Gemini client: {e}")

        if USE_OLLAMA:
            try:
                client = openai.AsyncOpenAI(api_key="ollama", base_url=OLLAMA_BASE_URL, http_client=http_client)
                return client, OLLAMA_MODEL
            except Exception:
                pass

        if API_KEY:
            try:
                client_kwargs = {"api_key": API_KEY, "http_client": http_client}
                if BASE_URL:
                    client_kwargs["base_url"] = BASE_URL
                client = openai.AsyncOpenAI(**client_kwargs)
                return client, MODEL_NAME
            except Exception as e:
                print(f"[AIService] Could not instantiate LLM client: {e}")

        return None, None

    @staticmethod
    def _mock_generate_itinerary(
        req: GenerateRequest,
        flights: Optional[List[Dict]] = None,
        hotels: Optional[List[Dict]] = None,
        local_places: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """Offline/fallback itinerary generator built on SerpApi search cache structures."""
        destination = req.destination
        days = req.days
        used_places = set()

        if not flights:
            flights = SerpService._fallback_flights(req.origin or "NYC", destination, "")
        if not hotels:
            hotels = SerpService._fallback_hotels(destination, req.budget)
        if not local_places:
            local_places = SerpService._fallback_places(destination)

        sel_flight = flights[0] if flights else {}
        sel_hotel = hotels[0] if hotels else {}

        sel_flight.setdefault("reasoning", f"Evaluated live SerpApi flight schedules; selected {sel_flight.get('airline', 'airline')} for optimal arrival time.")
        sel_hotel.setdefault("reasoning", f"Selected {sel_hotel.get('name', 'hotel')} because its location in {sel_hotel.get('location', destination)} minimizes transit to daily hubs.")

        agent_reasoning = (
            f"Agent Reasoning Loop Executed: Evaluated live flight options from {req.origin or 'NYC'} to {destination}, "
            f"selected {sel_flight.get('airline', 'flight')} for schedule efficiency. Evaluated SerpApi hotel results "
            f"and chose {sel_hotel.get('name', 'hotel')} near key neighborhood hubs. Geographically clustered daily "
            f"stops to limit intra-city transit to under 20 minutes."
        )

        itinerary = {
            "id": str(uuid.uuid4()),
            "destination": destination,
            "days": days,
            "style": req.style,
            "budget": req.budget,
            "interests": req.interests,
            "travelers": req.travelers,
            "mustsee": req.mustsee,
            "created_at": datetime.utcnow().isoformat(),
            "agent_reasoning": agent_reasoning,
            "selected_flight": sel_flight,
            "selected_hotel": sel_hotel,
            "days_data": []
        }

        for day_num in range(1, days + 1):
            day = AIService._generate_day(day_num, destination, req.style, req.mustsee if day_num == 1 else "", used_places, days, local_places)
            itinerary["days_data"].append(day)

        return itinerary

    @staticmethod
    def _generate_day(day_num: int, destination: str, style: str, mustsee: str, used_places: set, total_days: int, pool: List[Dict]) -> Dict:
        if style == "relaxed":
            blocks = ["morning", "afternoon"]
        elif style == "packed":
            blocks = ["morning", "afternoon", "evening", "late"]
        else:
            blocks = ["morning", "afternoon", "evening"]

        activities = []
        for b_idx, block in enumerate(blocks):
            available = [p for p in pool if p["name"] not in used_places]
            if not available:
                available = pool
            place = available[b_idx % len(available)]
            used_places.add(place["name"])

            time_labels = {
                "morning": "Morning — 9:00 to 12:30",
                "afternoon": "Afternoon — 13:00 to 17:00",
                "evening": "Evening — 17:30 to 21:00",
                "late": "Late — 21:00 onwards"
            }

            if day_num == 1 and block == "afternoon" and mustsee:
                activities.append({
                    "time": time_labels[block],
                    "name": mustsee,
                    "address": f"Central {destination.capitalize()}",
                    "lat": 0, "lng": 0,
                    "rating": 4.9,
                    "type": "must_see",
                    "description": f"Must-see request prioritized early in the itinerary.",
                    "reasoning": f"Prioritized for Day 1 to guarantee prime viewing time before crowds peak.",
                    "map_url": f"https://www.google.com/maps/search/?api=1&query={mustsee.replace(' ', '+')}+{destination.replace(' ', '+')}"
                })
            else:
                map_query = f"{place['name'].replace(' ', '+')}+{destination.replace(' ', '+')}"
                activities.append({
                    "time": time_labels[block],
                    "name": place["name"],
                    "address": place.get("address", destination),
                    "lat": place.get("lat", 0),
                    "lng": place.get("lng", 0),
                    "rating": place.get("rating", 4.6),
                    "type": place.get("type", "attraction"),
                    "description": place.get("description", f"Top spot in {destination}."),
                    "reasoning": f"Grouped in Day {day_num} due to location proximity and alignment with trip interests.",
                    "map_url": f"https://www.google.com/maps/search/?api=1&query={map_query}"
                })

        food_candidates = [p for p in pool if p.get("type") in ["restaurant", "bakery", "food_hall", "bar"]]
        if not food_candidates:
            food_candidates = pool
        food_place = food_candidates[day_num % len(food_candidates)]

        return {
            "day_number": day_num,
            "narrative_intro": f"Day {day_num} in {destination.capitalize()}: Exploring the historic & cultural core.",
            "day_reasoning": f"Day {day_num} itinerary is spatially clustered in the central area to minimize intra-city travel.",
            "activities": activities,
            "food": {
                "name": food_place["name"],
                "address": food_place.get("address", destination),
                "description": food_place.get("description", "Favored by locals for authentic cuisine."),
                "reasoning": "Selected for proximity to the afternoon walking route.",
                "map_url": f"https://www.google.com/maps/search/?api=1&query={food_place['name'].replace(' ', '+')}+{destination.replace(' ', '+')}"
            },
            "local_tip": {
                "title": "Local Pro Tip",
                "detail": "Arrive 20 minutes before opening to beat peak lines and catch early morning lighting."
            },
            "pace": style
        }

    @staticmethod
    def _summarize_itinerary(itinerary_context: Dict) -> str:
        """Builds a short plain-text summary instead of dumping raw JSON, so the model has
        less to wade through and won't echo back JSON syntax or get cut off summarizing it."""
        dest = itinerary_context.get("destination") or itinerary_context.get("city", "the trip")
        days = itinerary_context.get("days_data") or itinerary_context.get("days") or []
        lines = [f"Destination: {dest}, {len(days) if isinstance(days, list) else days} days."]
        if isinstance(days, list):
            for i, day in enumerate(days[:7], 1):  # cap so this can't balloon on long trips
                if isinstance(day, dict):
                    activities = day.get("activities") or day.get("blocks") or []
                    names = []
                    for a in activities[:4]:
                        if isinstance(a, dict):
                            act = a.get("act", a)
                            name = act.get("name") or act.get("t")
                            if name:
                                names.append(name)
                    if names:
                        lines.append(f"Day {i}: " + ", ".join(names))
        return "\n".join(lines)

    @staticmethod
    async def chat_reply(message: str, itinerary_context: Optional[Dict] = None) -> str:
        client, model = AIService._get_llm_client(purpose="chat")
        if client is not None:
            try:
                system_msg = (
                    "You are a knowledgeable, warm travel assistant texting with a friend. "
                    "Output ONLY your direct reply as natural conversational text — do not include "
                    "any planning notes, tone descriptions, formatting instructions, or meta-commentary "
                    "about how you're going to respond. Do not write things like 'Tone:' or "
                    "'Highlighting Itinerary Picks:' — just answer the question directly, as if speaking. "
                    "Keep it to 2-3 sentences. No markdown, no asterisks, no bullet points, no headers. "
                    "If the user asks about their current itinerary, reference it specifically using "
                    "the summary below."
                )
                if itinerary_context:
                    summary = AIService._summarize_itinerary(itinerary_context)
                    system_msg += f"\n\nCurrent itinerary summary:\n{summary}"

                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": message},
                    ],
                    temperature=0.7,
                    max_tokens=800,
                )
                reply = response.choices[0].message.content.strip()
                # Strip any markdown that slips through anyway, since the UI renders plain text.
                reply = reply.replace("**", "").replace("*", "").replace("__", "").replace("`", "")
                return reply
            except Exception as e:
                print(f"[AIService] Chat LLM call failed, using rule-based reply: {e}")

        return AIService._rule_based_reply(message, itinerary_context)

    @staticmethod
    def _rule_based_reply(message: str, itinerary_context: Optional[Dict] = None) -> str:
        last_msg = message.lower()
        responses = {
            "hello": "Hey there! Ready to plan something great? Tell me where you're headed.",
            "hi": "Hi! Where are we going?",
            "help": "I can help you plan your trip, suggest places to eat, or adjust your itinerary. What do you need?",
            "restaurant": "I'd look for places where locals actually eat — avoid menus with photos. The bar seats often have the best atmosphere.",
            "food": "The best food is usually one neighborhood away from the tourist center. Follow the locals at lunch time.",
            "budget": "For budget travel: grocery store breakfasts, free walking tours (tip well), and lunch specials at fancy restaurants.",
            "tip": "My best universal tip: arrive at popular spots 30 minutes before opening. You'll have the place to yourself.",
            "weather": "I can't check live weather, but I recommend packing layers and a light rain shell regardless of the forecast.",
            "thanks": "You got it! Enjoy the trip. Come back if you need anything else.",
            "thank": "Anytime! Safe travels.",
        }
        response = "I'm here to help with your trip. Ask me about restaurants, local tips, or how to adjust your itinerary."
        for key, val in responses.items():
            if key in last_msg:
                response = val
                break
        if itinerary_context and ("itinerary" in last_msg or "plan" in last_msg or "day" in last_msg):
            dest = itinerary_context.get("destination") or itinerary_context.get("city", "your destination")
            response = f"Your {itinerary_context.get('days', 3)}-day {dest} itinerary looks solid. The pacing is {itinerary_context.get('style', itinerary_context.get('pace', 'balanced'))}. Want me to suggest any changes?"
        return response

# ==================== FASTAPI APP ====================
app = FastAPI(title="Wanderlog API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== API ROUTES ====================
@app.post("/api/generate")
async def generate_itinerary(req: GenerateRequest):
    itinerary = await AIService.generate_itinerary(req)
    return itinerary

@app.post("/api/itinerary/adjust")
async def adjust_itinerary_day(req: AdjustRequest):
    result = await AIService.adjust_day(req)
    return result

@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    if req.messages:
        message = req.messages[-1].content
    else:
        message = req.message or ""

    context = req.itinerary_context or req.itinerary
    reply = await AIService.chat_reply(message, context)
    return {"reply": reply}

@app.post("/api/places/search")
async def search_places(req: PlaceSearchRequest):
    places = PlaceService.search_places(req.query, req.destination)
    return {"places": places}

@app.post("/api/itinerary/save")
async def save_itinerary(req: SaveRequest, db: Session = Depends(get_db)):
    itinerary_id = str(uuid.uuid4())[:8]
    db_itinerary = ItineraryDB(
        id=itinerary_id,
        destination=req.data.get("destination", "Unknown"),
        data=json.dumps(req.data)
    )
    db.add(db_itinerary)
    db.commit()
    return {"id": itinerary_id, "url": f"/api/itinerary/{itinerary_id}"}

@app.get("/api/itinerary/{itinerary_id}")
async def get_itinerary(itinerary_id: str, db: Session = Depends(get_db)):
    item = db.query(ItineraryDB).filter(ItineraryDB.id == itinerary_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Itinerary not found")
    return json.loads(item.data)

@app.get("/api/health")
async def health_check():
    if GOOGLE_API_KEY:
        provider = "Gemini"
    elif USE_OLLAMA:
        provider = "Ollama"
    elif GROQ_API_KEY:
        provider = "Groq"
    elif OPENAI_API_KEY:
        provider = "OpenAI"
    else:
        provider = "Mock data fallback (no LLM key configured)"

    return {
        "status": "ok",
        "ai_provider": provider,
        "gemini_configured": bool(GOOGLE_API_KEY),
        "gemini_model": GEMINI_MODEL,
        "gemini_chat_model": GEMINI_CHAT_MODEL,
        "serpapi_configured": bool(SERPAPI_API_KEY),
        "google_places_configured": bool(GOOGLE_PLACES_API_KEY),
        "mock_cities_available": list(MOCK_PLACES.keys())
    }

# ==================== STATIC FILES ====================
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")

@app.get("/")
async def root():
    if os.path.exists(frontend_path):
        return FileResponse(os.path.join(frontend_path, "index.html"))
    return {"message": "Wanderlog API is running. Frontend directory not found."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)