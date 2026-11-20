# Офлайн-справочник марок и моделей

Источник: https://github.com/serhatkildaci/global-car-models
Закреплённый commit: 44da5c9e5e0f3162d65579033f7a641473308b11 (последний commit источника 27 июня 2025).
Загружен: 14 ноября 2026. Лицензия MIT, полный текст сохранён без изменений.
Исходный файл: app/assets/car_models.json. SHA256: 42c74b1906cd43ff119081ad6dda1f015424b43c642c17eb436d5e252ec04f3b.
Сведения происхождения: app/assets/car_models.source.json.

Охват исходника: 94 марки и 1829 записей моделей. Это вспомогательные названия,
не исчерпывающий актуальный каталог всех лет, рынков, поколений и комплектаций.
Наличие названия не подтверждает сведения о конкретном автомобиле и не декодирует VIN.

Подсказки показывают английские названия из справочника и собственные названия
латиницей из сохранённых карточек. Модели фильтруются по марке. Известные варианты
вроде VW, «Фольксваген» и «Шкода» находят Volkswagen и Skoda при вводе.
Выбор марки сохраняет текст поля «Модель». Свободный ввод и прежние карточки
сохраняют написание владельца.

Каталог включён в Windows-сборку в _internal/app/assets вместе с лицензией.
Во время работы нет запросов к источнику, отправки машин/клиентов или требования интернета.
Для обновления снимка разработчик проверяет лицензию/схему, сохраняет commit и хеш,
проверяет подсказки и новую сборку. Автоматического сетевого обновления нет.


## Английские названия в 0.5.9
app/assets/car_models.overrides.json: точные соответствия по марке и исходному
названию; список targets содержит варианты выбора, aliases — известные написания.
Исходный JSON не редактируется. Подсказки строятся из исходника и локальных строк,
затем применяются соответствия и удаляются эквивалентные дубли. Карточки не мигрируют.
Слой включён в ZIP 0.5.9. Служебный запуск проверяет, что выпадающий список
в готовом EXE показывает английское название по русскому или прежнему вводу.

- BMW — 1 Series…8 Series, самостоятельные 1M, M3, M4, M5, M6, M8 и X3 M…X6 M.
- Mercedes-Benz — A-Class, C-Class, E-Class и остальные классы, отдельные
  обозначения CLA, GLK, SLK и др. Коды W123, W124, W126 и W140 доступны отдельно.
- Alfa Romeo — Brera и Spider разделены, Alphasud исправлено на Alfasud.
- Renault — исправлено Treiber на Triber.

Проверенные страницы для исправлений (18 ноября 2026):
- BMW, написание 3 Series: https://www.bmw.com/content/dam/bmw/marketAU/bmw_com/all-models/specification_guide_2021/August/BMW_3_Series_Sedan_Specification_Guide-G20.pdf.asset.1629454276010.pdf
- BMW M, архив моделей: https://www.bmw-m.com/en/topics/m-heritage.html
- BMW 1 Series M Coupé: https://www.bmw-m.com/en/topics/magazine-article-pool/bmw-1er-m-coupe.html
- Mercedes-Benz, классы: https://group.mercedes-benz.com/company/divisions/mercedes-benz-cars/
- Mercedes-Benz, модельный ряд: https://www.mbusa.com/en/all-vehicles
- Mercedes-Benz, E-Class с 1993 года: https://media.mbusa.com/news/a-bridge-between-tradition-and-digitalization-the-new-e-class
- Renault Triber: https://www.renault.co.in/cars/renault-triber.html
- Alfa Romeo Alfasud: https://www.media.stellantis.com/em-en/alfa-romeo/press/storie-alfa-romeo-eighth-episode-design-sportiness-and-innovation-the-156-was-an-authentically-successful-alfa-romeo
- Написание Tofaş, Şahin и Doğan: https://ir.tofas.com.tr/2022/en/m-1-4.html

Проверены все 94 марки и 1829 исходных строк. После исправлений доступны 1841
название модели, считая отдельно модели разных марок. Тест проверяет покрытие
всех исходных строк, латиницу и отсутствие `Serisi` и дублей. Написания Fiat X1/9
и Mercedes-Benz 190 (W201), диакритика Citroën, Sián, Mégane, Doğan, Serçe и Şahin
сохранены.

При проверке 18 ноября Git checkout использовал CRLF. Байтовый SHA256 исходника
cd53d1d1290b78302feb1660050469817cccd4f50ffad9ebb2db81ee5762d131.
При нормализации CRLF→LF он совпадает с сохранённым исходным SHA256 выше.
