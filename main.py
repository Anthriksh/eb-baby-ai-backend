from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
import httpx

from database import Base, engine, get_db
import models

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Web Baby AI - Backend with Commands")


# ---------------- Pydantic Models ----------------

class TeachRequest(BaseModel):
    word: str
    true_label: str


class CommandRequest(BaseModel):
    command: str  # e.g. "go learn how to cook butter chicken"


# ---------------- Helper: fetch from internet ----------------

async def fetch_wikipedia_summary(topic: str) -> tuple[str, bool, str | None]:
    """
    Try to fetch a summary for a topic from Wikipedia.
    Returns (summary, from_internet, error_message)
    """
    wiki_topic = topic.replace(" ", "_")
    url = (
        "https://en.wikipedia.org/w/api.php"
        f"?action=query&format=json&prop=extracts&exintro&explaintext&titles={wiki_topic}"
    )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url)

        if r.status_code != 200:
            return (
                f"(Fallback) Could not fetch live data for '{topic}'. HTTP {r.status_code}.",
                False,
                f"HTTP {r.status_code}",
            )

        data = r.json()
        pages = data.get("query", {}).get("pages", {})
        if not pages:
            return (
                f"(Fallback) No Wikipedia page found for '{topic}'.",
                False,
                "No pages in response",
            )

        page = next(iter(pages.values()))
        summary = page.get("extract") or ""
        if not summary:
            return (
                f"(Fallback) Wikipedia page for '{topic}' has no extract.",
                False,
                "Empty extract",
            )

        return summary, True, None

    except Exception as e:
        return (
            f"(Fallback) Error fetching data for '{topic}': {e}",
            False,
            str(e),
        )


async def fetch_recipe(dish: str) -> tuple[str, bool, str | None]:
    """
    Try to fetch a recipe using TheMealDB (free public API).
    Returns (recipe_text, from_internet, error_message)
    """
    url = f"https://www.themealdb.com/api/json/v1/1/search.php?s={dish}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url)

        if r.status_code != 200:
            return (
                f"(Fallback) Could not fetch live recipe for '{dish}'. HTTP {r.status_code}.",
                False,
                f"HTTP {r.status_code}",
            )

        data = r.json()
        meals = data.get("meals")
        if not meals:
            return (
                f"(Fallback) No recipe found online for '{dish}'.",
                False,
                "No meals in response",
            )

        meal = meals[0]
        name = meal.get("strMeal", dish)
        category = meal.get("strCategory", "")
        area = meal.get("strArea", "")
        instructions = meal.get("strInstructions", "")

        recipe_text = f"Recipe for {name} ({category}, {area}):\n\n{instructions}"

        return recipe_text, True, None

    except Exception as e:
        return (
            f"(Fallback) Error fetching recipe for '{dish}': {e}",
            False,
            str(e),
        )


def save_web_knowledge(
    db: Session,
    topic: str,
    content: str,
    source: str,
):
    record = (
        db.query(models.WebKnowledge)
        .filter(models.WebKnowledge.topic == topic)
        .first()
    )

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
def read_root():
    return {"message": " Baby AI backend is running with command support!"}


@app.get("/health")
def health_check():
    return {"status": "ok"}


# ---------------- Manual Teaching ----------------

@app.post("/teach")
def teach_baby(req: TeachRequest, db: Session = Depends(get_db)):

    concept = (
        db.query(models.Concept)
        .filter(models.Concept.word == req.word)
        .first()
    )

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

    return {
        "message": "Baby learned something from you!",
        "word": concept.word,
        "label": concept.label,
        "seen_count": concept.seen_count,
        "correct_count": concept.correct_count,
    }


# ---------------- Command Endpoint (NATURAL LANGUAGE) ----------------

@app.post("/command")
async def run_command(req: CommandRequest, db: Session = Depends(get_db)):
    """
    Understand a simple natural-language command and either:
      - fetch a recipe
      - fetch a generic topic summary
    Then store it in WebKnowledge and return info about what happened.
    """

    text = req.command.lower().strip()
    if not text:
        raise HTTPException(status_code=400, detail="Command cannot be empty.")

    # very simple intent detection:
    is_recipe = ("cook" in text) or ("recipe" in text) or ("make" in text)

    # naive extraction:
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
        dish_or_topic = dish_or_topic[:-1].strip()

    if not dish_or_topic:
        raise HTTPException(status_code=400, detail="Could not detect topic/dish from command.")

    if is_recipe:
        content, from_internet, error_msg = await fetch_recipe(dish_or_topic)
        source = "recipe_api" if from_internet else "recipe_api_fallback"
    else:
        content, from_internet, error_msg = await fetch_wikipedia_summary(dish_or_topic)
        source = "wikipedia" if from_internet else "wikipedia_fallback"

    record = save_web_knowledge(db, dish_or_topic, content, source)

    return {
        "message": "Command processed.",
        "original_command": req.command,
        "detected_type": "recipe" if is_recipe else "topic",
        "topic_or_dish": dish_or_topic,
        "from_internet": from_internet,
        "debug_error": error_msg,
        "stored_summary_preview": record.summary[:350],
    }


# ---------------- Read Stored Knowledge ----------------

@app.get("/knowledge/{topic}")
def get_knowledge(topic: str, db: Session = Depends(get_db)):
    record = (
        db.query(models.WebKnowledge)
        .filter(models.WebKnowledge.topic == topic)
        .first()
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"No stored knowledge for topic '{topic}'. Use /command first.",
        )

    return {
        "topic": record.topic,
        "source": record.source,
        "summary": record.summary,
    }
