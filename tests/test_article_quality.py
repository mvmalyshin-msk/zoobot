"""Качество статей: читаемость, длина абзацев, ссылки, CTA, длина title/description.

Читаемость — Flesch-Kincaid Grade с коэффициентами для русского языка (проект Plain Russian Language,
те же, что в библиотеке ruTS): 0.49·(слов в предложении) + 7.3·(слогов в слове) − 16.59.
Цель — текст не сложнее 9 класса школы."""
import os
import re
import unittest

os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_quality_test.db")

import articles

VOWELS = set("аеёиоуыэюяaeiouy")
WORD = re.compile(r"[А-Яа-яЁёA-Za-z]+")
LINKS = re.compile(r"«[^»]{8,}»")  # ссылки на другие статьи в тексте — в «кавычках-ёлочках»


def text_of(a: dict) -> str:
    parts = [a["lead"]]
    for s in a["sections"]:
        parts.append(s.get("body", ""))
    for q, ans in a["faq"]:
        parts.append(ans)
    t = "\n".join(parts)
    t = re.sub(r"\{\{[^}]+\}\}", "1000", t)
    return re.sub(r"^### .*$", "", t, flags=re.M)


def grade(text: str) -> float:
    sents = [s for s in re.split(r"[.!?…]+\s|\n+", text) if WORD.search(s)]
    words = WORD.findall(text)
    syl = sum(max(1, sum(ch in VOWELS for ch in w.lower())) for w in words)
    return 0.49 * len(words) / len(sents) + 7.3 * syl / len(words) - 16.59


def modern():
    """Статьи нового формата : у них есть description, faq, sources, CTA."""
    return {p: a for p, a in articles.ARTICLES.items() if "description" in a and "sources" in a}


class ArticleQuality(unittest.TestCase):
    def test_readability(self):
        for path, a in articles.ARTICLES.items():
            with self.subTest(path=path):
                g = grade(text_of(a))
                self.assertLess(g, 9.0, f"{path}: текст сложный (класс {g:.1f}) — короче фразы, проще слова")

    def test_paragraphs_short(self):
        for path, a in articles.ARTICLES.items():
            for s in a["sections"]:
                for p in s.get("body", "").split("\n\n"):
                    with self.subTest(path=path, h=s["h"]):
                        self.assertLessEqual(len(WORD.findall(p)), 90, f"{path}: абзац длиннее 90 слов")

    def test_meta_lengths(self):
        for path, a in modern().items():
            with self.subTest(path=path):
                self.assertLessEqual(len(a["title"]), 60)
                self.assertLessEqual(len(a["description"]), 160)

    def test_structure(self):
        for path, a in modern().items():
            with self.subTest(path=path):
                self.assertGreaterEqual(len(a["faq"]), 6, "нужно 6 частых вопросов")
                self.assertGreaterEqual(len(a["sections"]), 3)
                self.assertTrue(a.get("cta_text") and a.get("service"), "нужен призыв к заявке")
                self.assertIn("{{", a["lead"], "в первом абзаце нужна наша цена")
                self.assertGreaterEqual(len(a["sources"]), 1)

    def test_internal_links(self):
        new = [p for p, a in articles.ARTICLES.items() if a.get("published", "") >= "2026-11-19"]
        for path in new:
            with self.subTest(path=path):
                self.assertGreaterEqual(len(LINKS.findall(text_of(articles.ARTICLES[path]))), 2,
                                        "сошлитесь хотя бы на 2 другие статьи")


if __name__ == "__main__":
    unittest.main()
