import spacy
from word2number import w2n  # type: ignore


def extract_numbers(nlp_model: spacy.language.Language, text: str) -> list[int]:
    doc = nlp_model(text)
    numbers = []

    for token in doc:
        if token.like_num:
            try:
                # Convert number words to digits
                number = w2n.word_to_num(token.text)
            except ValueError:
                # Directly parse digits
                number = int(token.text) if token.text.isdigit() else token.text
            numbers.append(number)

    return numbers
