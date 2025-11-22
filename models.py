from sqlalchemy import Column, Integer, String, Boolean, Text
from database import Base


# This table stores what the baby knows about each word (manual teaching)
class Concept(Base):
    __tablename__ = "concepts"

    id = Column(Integer, primary_key=True, index=True)
    word = Column(String, index=True, unique=True)   # e.g. "cat"
    label = Column(String)                           # e.g. "animal" or "safe"
    seen_count = Column(Integer, default=0)          # how many times we've used/taught this word
    correct_count = Column(Integer, default=0)       # how many times baby got it right


# This table stores each teaching experience
class Experience(Base):
    __tablename__ = "experiences"

    id = Column(Integer, primary_key=True, index=True)
    word = Column(String)                            # word shown
    true_label = Column(String)                      # correct label
    ai_guess = Column(String)                        # what baby guessed
    was_correct = Column(Boolean, default=False)     # whether guess was right


# This table stores information learned from the internet (e.g. Wikipedia)
class WebKnowledge(Base):
    __tablename__ = "web_knowledge"

    id = Column(Integer, primary_key=True, index=True)
    topic = Column(String, index=True, unique=True)  # e.g. "black hole"
    source = Column(String, default="wikipedia")     # where it came from
    summary = Column(Text)                           # text summary of the topic