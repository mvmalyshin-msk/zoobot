"""Фильтр мата: ловит прямой мат, обходы (пробелы, точки, звёздочки, латиница, растяжки), не трогает обычные слова."""
import unittest

import profanity

BAD = ["хуй", "ХУЙ", "х у й", "хуууй", "х.у.й", "пиздец", "п*здец", "ебать", "ёбаный", "заебал", "уебок",
       "съебал", "блядь", "бл*дь", "бляяя", "пидор", "пидорас", "мудак", "залупа", "huy", "xuy", "fuck you",
       "долбоёб", "говно", "срал", "ты охуел", "иди нахуй", "Ленина 5, пиздец", "нахер хуйня", "хуета",
       "еб твою мать", "ёб твою"]
GOOD = ["ребёнок", "хлеб", "небо", "вебинар", "учебник", "Херсон", "мандарин", "сука", "оскорбление",
        "употреблять", "хутор", "хулиган", "потребитель", "серебро", "лебедь", "колебание", "требуется",
        "гребень", "дебют", "Тверская улица, 12", "Ленинский проспект, 10, кв. 5", "Рекс, собака, лабрадор, 3 года",
        "Анна", "характер спокойный, любит гулять", "вход со двора", "кв 5 д 7", "Москва слезам не верит",
        "ёжик", "ёлка", "небольшой", "хорошая собака", "худой кот", "хуже не бывает", "манго", "сребро",
        "дом 5 к 2 с 1", "бледный", "блин", "блистер"]


class Profanity(unittest.TestCase):
    def test_bad(self):
        for t in BAD:
            self.assertTrue(profanity.has_profanity(t), t)

    def test_good(self):
        for t in GOOD:
            self.assertFalse(profanity.has_profanity(t), t)

    def test_empty(self):
        self.assertFalse(profanity.has_profanity(""))
        self.assertFalse(profanity.has_profanity(None))

    def test_js_spec_matches(self):
        spec = profanity.js_spec()
        self.assertIn("patterns", spec)
        self.assertTrue(all(isinstance(p, str) for p in spec["patterns"]))


if __name__ == "__main__":
    unittest.main()
