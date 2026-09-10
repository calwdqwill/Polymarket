# Правила работы с poly_crypto

10 сентября выполнен target-profit research V1: результаты в `target-profit-research-v1/`. Dynamic-Q baseline $0,50/$1 убыточен; Q10/$0,50/edge 10–15c рекомендован для следующего независимого read-only shadow. PROCEED TO LIVE SHADOW — рекомендация; shadow/collector/orders не запущены. После отчёта работа остановлена, новый запуск требует отдельного задания. Ограничения settlement/fees сохраняются.

Развивай существующий проект небольшими проверяемыми итерациями. Перед новой фазой прочитай PROJECT_PLAN.md, BACKLOG.md, ARCHITECTURE.md, README.md и CHANGELOG.md, изучи связанные файлы и состояние Git. Сохраняй изменения пользователя.

Все Markdown-документы пиши на русском языке. Код, имена API-полей, переменные и команды сохраняй в принятом стиле проекта. Объясняй технические решения понятно, с разделением фактов, допущений и рисков.

После значимой итерации обновляй CHANGELOG.md и BACKLOG.md; при необходимости PROJECT_PLAN.md, ARCHITECTURE.md и README.md. Выполняй релевантные тесты и Ruff. Не меняй стек, публичные контракты, структуру основной БД или торговые формулы без основания и явного согласованного задания.

Текущая prediction-фаза — публичный read-only BTC 5m research. Не отправляй orders, не подключай wallets/private keys, не добавляй maker/taker execution, rebates, capital allocation или внешний hedge. Execution simulator разрешается только отдельным следующим заданием после оценки многочасовых данных.

Заданием 10 сентября разрешён и выполнен offline execution simulator V1 на recovery dataset; результаты находятся в `execution-sim-v1/`. Это не разрешение на paper/live execution. Итог CONTINUE RESEARCH; точные fees и settlement остаются неподтверждёнными.

Реальные данные, synthetic fixtures и гипотезы должны быть явно разделены. Для VWAP/edge/capacity используй Decimal. Не суммируй зеркальную NO-ликвидность Limitless как второй пул. UNKNOWN settlement допускает сбор, но не гарантированный арбитраж; NOT_EQUIVALENT блокирует сравнение бинарной выплаты.

Raw transport сохраняй до применения, с session/connection/ordinal и UTC/monotonic clocks. Не снимай BBO safety checks ради coverage. Разделяй freshness книги и здоровье соединения; не продлевай валидность или положительные эпизоды через разрывы данных.

Не выдавай запланированные часы за собранный dataset. Проверяй фактическое завершение, replay и coverage. Не удаляй raw для экономии диска без явного запроса. Текущие команды и ограничения описаны в docs/prediction-collector-runbook.md.
