from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel
import httpx

from database import Base, engine, get_db
import models

# --------- DB setup ---------
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Web Baby AI - Backend (Anthriksh)")

# --------- CORS (IMPORTANT for frontend) ---------
# For now allow all origins so any frontend can call it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # if you want to lock later, replace with your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------- Pydantic models ---------

class TeachRequest(BaseModel):
    word: str
    true_label: str


class CommandRequest(BaseModel):
    command: str  # e.g. "learn about black holes"


class KnowledgeItem(BaseModel):
    topic: str
    source: str
    summary: str


class Stats(BaseModel):
    concepts: int
    experiences: int
    knowledge_items: int


# --------- Internet helpers ---------

async def fetch_duckduckgo_summary(topic: str) -> tuple[str, bool, Optional[str]]:
    """
    Fetch a short summary using DuckDuckGo Instant Answer API.
    Returns (summary, from_internet, error_message).
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
                f"(Fallback) DuckDuckGo HTTP {r.status_code} for '{topic}'.",
                False,
                f"HTTP {r.status_code}",
            )

        data = r.json()
        abstract = data.get("AbstractText", "")

        if not abstract:
            return (
                f"(Fallback) No direct summary found for '{topic}'.",
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


async def fetch_recipe(dish: str) -> tuple[str, bool, Optional[str]]:
    """
    Fetch a recipe using TheMealDB free API.
    Returns (recipe_text, from_internet, error_message).
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
                f"(Fallback) No recipe found online for '{dish}'.",
                False,
                "No meals in response",
            )

        meal = meals[0]
        name = meal.get("strMeal", dish)
        category = meal.get("strCategory", "") or ""
        area = meal.get("strArea", "") or ""
        instructions = meal.get("strInstructions", "") or ""

        recipe_text = f"Recipe for {name} ({category} {area}).\n\n{instructions}"
        return recipe_text, True, None

    except Exception as e:
        return (
            f"(Fallback) Error fetching recipe for '{dish}': {e}",
            False,
            str(e),
        )


# --------- DB helper ---------

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


# --------- Basic routes ---------

@app.get("/")
def read_root():
    return {"message": "Web Baby AI backend is running – created for Anthriksh 👶🧠"}


@app.get("/health")
def health_check():
    return {"status": "ok"}


# --------- Manual teaching ---------

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


# --------- Command endpoint ---------

@app.post("/command")
async def run_command(req: CommandRequest, db: Session = Depends(get_db)):
    """
    Understand a simple natural-language command and either:
      - fetch a recipe
      - fetch a topic summary (via DuckDuckGo)
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
        "what is",
        "explain",
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
        content, from_internet, error_msg = await fetch_duckduckgo_summary(dish_or_topic)
        source = "duckduckgo" if from_internet else "duckduckgo_fallback"

    record = save_web_knowledge(db, dish_or_topic, content, source)

    return {
        "message": "Command processed.",
        "original_command": req.command,
        "detected_type": "recipe" if is_recipe else "topic",
        "topic_or_dish": dish_or_topic,
        "from_internet": from_internet,
        "source": source,
        "debug_error": error_msg,
        "stored_summary_preview": record.summary[:350],
    }


# --------- Knowledge browsing ---------

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


@app.get("/knowledge")
def list_knowledge(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    records = db.query(models.WebKnowledge).all()
    # just return last N (if you want ordering, you can change models later)
    records = records[-limit:]
    return [
        {"topic": r.topic, "source": r.source, "summary": r.summary}
        for r in records
    ]


@app.delete("/knowledge/{topic}")
def delete_knowledge(topic: str, db: Session = Depends(get_db)):
    record = (
        db.query(models.WebKnowledge)
        .filter(models.WebKnowledge.topic == topic)
        .first()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    db.delete(record)
    db.commit()
    return {"message": f"Deleted knowledge for '{topic}'"}


# --------- Concepts, experiences, stats ---------

@app.get("/concepts")
def list_concepts(db: Session = Depends(get_db)):
    concepts = db.query(models.Concept).all()
    return [
        {
            "word": c.word,
            "label": c.label,
            "seen_count": c.seen_count,
            "correct_count": c.correct_count,
        }
        for c in concepts
    ]


@app.get("/experiences")
def list_experiences(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)):
    exps = db.query(models.Experience).all()
    exps = exps[-limit:]
    return [
        {
            "word": e.word,
            "true_label": e.true_label,
            "ai_guess": e.ai_guess,
            "was_correct": e.was_correct,
        }
        for e in exps
    ]


@app.get("/stats", response_model=Stats)
def stats(db: Session = Depends(get_db)):
    concepts = db.query(models.Concept).count()
    experiences = db.query(models.Experience).count()
    knowledge_items = db.query(models.WebKnowledge).count()
    return Stats(
        concepts=concepts,
        experiences=experiences,
        knowledge_items=knowledge_items,
    )


@app.post("/reset_all")
def reset_all(
    confirm: str = Query(..., description="Must be 'yes_i_am_sure'"),
    db: Session = Depends(get_db),
):
    if confirm != "yes_i_am_sure":
        raise HTTPException(status_code=400, detail="Confirmation phrase incorrect.")

    # delete in safe order
    db.query(models.Experience).delete()
    db.query(models.Concept).delete()
    db.query(models.WebKnowledge).delete()
    db.commit()

    return {"message": "All baby data wiped. Starting fresh."}
