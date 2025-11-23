from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
import httpx
import re

from database import Base, engine, get_db
import models

# ---------------- CONFIG ----------------
# Your personal proxy URL (LocalTunnel)
PROXY_BASE_URL = "https://early-beans-shout.loca.lt"

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Web Baby AI - Backend with Commands + Personal Proxy + Multi-Source")


# ---------------- Pydantic Models ----------------

class TeachRequest(BaseModel):
    word: str
    true_label: str


class CommandRequest(BaseModel):
    command: str


# ---------------- Small helper: clean HTML to text ----------------

def clean_html_to_text(html: str) -> str:
    """
    Remove scripts/styles/tags and compress whitespace.
    Not perfect, but good enough to turn pages into readable text.
    """
    # remove script and style blocks
    text = re.sub(r"(?is)<script.*?>.*?</script>", "", html)
    text = re.sub(r"(?is)<style.*?>.*?</style>", "", text)
    # remove all tags
    text = re.sub(r"<[^>]+>", " ", text)
    # collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # limit length (so response isn't huge)
    return text[:4000]


# ---------------- Helper: DuckDuckGo Instant Answer API ----------------

async def fetch_summary_from_duckduckgo(topic: str) -> tuple[str, bool, str | None]:
    """
    Try to get a clean summary from DuckDuckGo Instant Answer API.
    Returns (summary_text, from_internet, error_message).
    """
    url = "https://api.duckduckgo.com/"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                url,
                params={
                    "q": topic,
                    "format": "json",
                    "no_html": 1,
                    "skip_disambig": 1,
                },
            )

        if r.status_code != 200:
            return (
                f"(Fallback) DuckDuckGo HTTP {r.status_code} while fetching '{topic}'.",
                False,
                f"HTTP {r.status_code}",
            )

        data = r.json()
        abstract = data.get("AbstractText", "")

        if not abstract:
            return (
                f"(Fallback) DuckDuckGo had no direct summary for '{topic}'.",
                False,
                "Empty abstract",
            )

        return abstract, True, None

    except Exception as e:
        return (
            f"(Fallback) Error calling DuckDuckGo for '{topic}': {e}",
            False,
            str(e),
        )


# ---------------- Helper: fetch via YOUR proxy ----------------

async def fetch_via_personal_proxy(topic: str) -> tuple[str, bool, str | None]:
    """
    Ask your local proxy (running on your laptop) to fetch content for a topic.
    Then clean HTML into text.
    """
    if not PROXY_BASE_URL:
        return (
            "(Fallback) Proxy base URL is not set.",
            False,
            "Proxy URL missing",
        )

    url = f"{PROXY_BASE_URL}/fetch"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, params={"topic": topic})

        if r.status_code != 200:
            return (
                f"(Fallback) Proxy HTTP {r.status_code} for '{topic}'.",
                False,
                f"HTTP {r.status_code}",
            )

        data = r.json()

        if "error" in data and data["error"]:
            return (
                f"(Fallback) Proxy error for '{topic}': {data['error']}",
                False,
                data["error"],
            )

        raw_html = data.get("content", "")
        if not raw_html:
            return (
                f"(Fallback) Proxy returned no content for '{topic}'.",
                False,
                "Empty content",
            )

        cleaned = clean_html_to_text(raw_html)
        if not cleaned:
            return (
                f"(Fallback) Proxy content for '{topic}' could not be cleaned.",
                False,
                "Empty cleaned text",
            )

        return cleaned, True, None

    except Exception as e:
        return (
            f"(Fallback) Error contacting proxy for '{topic}': {e}",
            False,
            str(e),
        )


# ---------------- Multi-source topic fetcher ----------------

async def fetch_topic_knowledge(topic: str) -> tuple[str, bool, str | None, str]:
    """
    Try multiple sources in order:
      1) DuckDuckGo Instant Answer API (short summary)
      2) Personal proxy (your laptop, full web page -> cleaned)

    Returns: (content, from_internet, error_message, source_name)
    """
    # 1) Try DuckDuckGo API
    content, ok, err = await fetch_summary_from_duckduckgo(topic)
    if ok:
        return content, True, None, "duckduckgo_api"

    # 2) Fallback to your proxy
    content2, ok2, err2 = await fetch_via_personal_proxy(topic)
    if ok2:
        return content2, True, None, "personal_proxy"

    # If both fail, return the last error
    return content2, False, err2, "topic_fallback"


# ---------------- Helper: recipe API ----------------

async def fetch_recipe(dish: str) -> tuple[str, bool, str | None]:
    """
    Fetch a recipe using TheMealDB API.
    """
    url = f"https://www.themealdb.com/api/json/v1/1/search.php?s={dish}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url)

        if r.status_code != 200:
            return (
                f"(Fallback) Could not fetch recipe for '{dish}'. HTTP {r.status_code}.",
                False,
                f"HTTP {r.status_code}",
            )

        data = r.json()
        meals = data.get("meals")

        if not meals:
            return (
                f"(Fallback) No recipe found for '{dish}'.",
                False,
                "No meals",
            )

        meal = meals[0]
        name = meal.get("strMeal", dish)
        category = meal.get("strCategory", "")
        area = meal.get("strArea", "")
        instructions = meal.get("strInstructions", "")

        recipe = f"Recipe for {name} ({category}, {area}):\n\n{instructions}"

        return recipe, True, None

    except Exception as e:
        return (
            f"(Fallback) Error fetching recipe for '{dish}': {e}",
            False,
            str(e),
        )


# ---------------- DB Save Helper ----------------

def save_web_knowledge(
    db: Session,
    topic: str,
    content: str,
    source: str,
):
    record = db.query(models.WebKnowledge).filter(models.WebKnowledge.topic == topic).first()

    if record is None:
        record = models.WebKnowledge(
            topic=topic,
            source=source,
            summary=content,
        )
        db.add(record)
    else:
        record.summary = content
        record.source = source

    db.commit()
    db.refresh(record)
    return record


# ---------------- Basic Routes ----------------

@app.get("/")
def root():
    return {"message": "Baby AI backend running with multi-source internet access (DuckDuckGo + personal proxy)!"}


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------- Manual Teaching ----------------

@app.post("/teach")
def teach(req: TeachRequest, db: Session = Depends(get_db)):

    concept = db.query(models.Concept).filter(models.Concept.word == req.word).first()

    if concept is None:
        concept = models.Concept(
            word=req.word,
            label=req.true_label,
            seen_count=1,
            correct_count=1,
        )
        db.add(concept)
    else:
        concept.label = req.true_label
        concept.seen_count += 1
        concept.correct_count += 1

    exp = models.Experience(
        word=req.word,
        true_label=req.true_label,
        ai_guess=req.true_label,
        was_correct=True,
    )
    db.add(exp)

    db.commit()
    db.refresh(concept)

    return {"message": "Baby learned!", "word": concept.word, "label": concept.label}


# ---------------- COMMAND ENDPOINT ----------------

@app.post("/command")
async def run_command(req: CommandRequest, db: Session = Depends(get_db)):

    text = req.command.lower().strip()
    if not text:
        raise HTTPException(400, "Command cannot be empty.")

    is_recipe = any(word in text for word in ["cook", "recipe", "make"])

    # Extract topic/dish
    dish_or_topic = text
    for prefix in [
        "go and learn how to cook",
        "learn how to cook",
        "how to cook",
        "learn recipe for",
        "recipe for",
        "learn about",
        "know about",
        "learn",
    ]:
        if dish_or_topic.startswith(prefix):
            dish_or_topic = dish_or_topic[len(prefix):].strip()

    if dish_or_topic.endswith("."):
        dish_or_topic = dish_or_topic[:-1]

    if not dish_or_topic:
        raise HTTPException(400, "Could not detect topic/dish.")

    # FETCH INTERNET DATA
    if is_recipe:
        content, ok, err = await fetch_recipe(dish_or_topic)
        source = "recipe_api" if ok else "recipe_fallback"
    else:
        content, ok, err, source = await fetch_topic_knowledge(dish_or_topic)

    # SAVE IN DB
    record = save_web_knowledge(db, dish_or_topic, content, source)

    return {
        "message": "Command processed.",
        "original_command": req.command,
        "topic_or_dish": dish_or_topic,
        "from_internet": ok,
        "source": source,
        "debug_error": err,
        "stored_summary_preview": record.summary[:350],
    }


# ---------------- Read Stored Knowledge ----------------

@app.get("/knowledge/{topic}")
def get_knowledge(topic: str, db: Session = Depends(get_db)):
    record = db.query(models.WebKnowledge).filter(models.WebKnowledge.topic == topic).first()

    if record is None:
        raise HTTPException(404, f"No stored knowledge for '{topic}'")

    return {
        "topic": record.topic,
        "source": record.source,
        "summary": record.summary,
    }
