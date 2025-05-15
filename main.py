from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import cloudscraper
from typing import List
import chess.pgn
import io
from stockfish import Stockfish
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing

app = FastAPI(title="Chess.com Cheater Detector API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STOCKFISH_PATH = "./bin/stockfish"
scraper = cloudscraper.create_scraper()


def fetch_archives(username: str) -> List[str]:
    url = f"https://api.chess.com/pub/player/{username.lower()}/games/archives"
    res = scraper.get(url)
    if res.status_code != 200:
        print(f"[DEBUG] Erro ao buscar arquivos: {res.status_code} - {res.text}")
        return []
    return res.json().get("archives", [])


def fetch_games_from_archive(archive_url: str) -> List[dict]:
    res = scraper.get(archive_url)
    if res.status_code != 200:
        print(f"[DEBUG] Erro ao buscar partidas no arquivo: {res.status_code} - {res.text}")
        return []
    return res.json().get("games", [])


def analyze_game_process_safe(game_data: tuple) -> dict:
    game, username, stockfish_path = game_data
    stockfish = Stockfish(path=stockfish_path)

    opponent = game["white"]["username"] if game["black"]["username"] == username else game["black"]["username"]
    result = game["white"]["result"] if game["white"]["username"] == username else game["black"]["result"]
    user_color = "white" if game["white"]["username"] == username else "black"

    pgn_text = game.get("pgn", "")
    best_moves = 0
    total_moves = 0

    if pgn_text:
        game_pgn = chess.pgn.read_game(io.StringIO(pgn_text))
        board = game_pgn.board()
        for move in game_pgn.mainline_moves():
            board.push(move)
            total_moves += 1
            if (user_color == "white" and board.turn == chess.BLACK) or (user_color == "black" and board.turn == chess.WHITE):
                continue
            stockfish.set_fen_position(board.fen())
            best = stockfish.get_best_move()
            if best == move.uci():
                best_moves += 1

    precisao = round((best_moves / total_moves) * 100, 2) if total_moves else 0

    return {
        "oponente": opponent,
        "resultado": result,
        "precisao": precisao,
        "url": game["url"]
    }


def avaliar_suspeita(jogos: List[dict]) -> bool:
    total = len(jogos)
    if total == 0:
        return False

    # Calcular métricas básicas
    vitorias = sum(1 for j in jogos if j["resultado"] == "win")
    media_precisao = sum(j["precisao"] for j in jogos) / total
    partidas_precisas = sum(1 for j in jogos if j["precisao"] > 90)

    # Converter para proporções
    vitorias_pct = vitorias / total
    precisao_alta_pct = partidas_precisas / total

    # Heurística com pesos
    score = (
        (media_precisao / 100) * 0.5 +  # precisão média
        vitorias_pct * 0.3 +           # taxa de vitória
        precisao_alta_pct * 0.2        # % partidas muito precisas
    ) * 100  # escala de 0 a 100

    print(f"[DEBUG] Score de suspeita: {score:.2f}")
    return score >= 75  # limiar arbitrário (pode ajustar)


@app.get("/analisar/{username}")
def analisar_usuario(username: str):
    archives = fetch_archives(username)
    print(f"[DEBUG] Arquivos encontrados: {len(archives)}")

    if not archives:
        return {"erro": "Usuário não encontrado ou sem partidas."}

    partidas = []
    max_partidas = 10

    for archive in reversed(archives):
        games = fetch_games_from_archive(archive)
        for game in games:
            if len(partidas) >= max_partidas:
                break
            if "white" in game and "black" in game:
                usernames = [game["white"]["username"].lower(), game["black"]["username"].lower()]
                if username.lower() in usernames:
                    partidas.append((game, username, STOCKFISH_PATH))
        if len(partidas) >= max_partidas:
            break

    detalhes = []
    with ProcessPoolExecutor(max_workers=multiprocessing.cpu_count()) as executor:
        futures = [executor.submit(analyze_game_process_safe, partida) for partida in partidas]

        for future in as_completed(futures):
            try:
                detalhes.append(future.result())
            except Exception as e:
                print(f"[ERRO] Falha ao analisar partida: {e}")

    if not detalhes:
        return {"erro": "Nenhuma partida encontrada para este usuário."}

    suspeito = avaliar_suspeita(detalhes)

    return {
        "usuario": username,
        "jogos_analisados": len(detalhes),
        "detalhes": detalhes,
        "suspeito": suspeito
    }
