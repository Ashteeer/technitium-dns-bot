# RELEASE.md — выпуск версий и обновление репозитория

Как публиковать изменения и релизы Technitium DNS bot на GitHub
(`Ashteeer/technitium-dns-bot`) через GitHub CLI (`gh`).

---

## Модель версий

- Версия хранится в [src/ttbot/__init__.py](../src/ttbot/__init__.py) (`__version__`)
  и тянется в пакет через `pyproject.toml` (`dynamic = ["version"]`).
- Релиз = git-тег с **числовым** именем `X.Y.Z` (semver, без префикса `v`) +
  GitHub Release с тем же именем.
- `INSTALL.sh` на сервере определяет «последнюю версию» по числовым тегам
  (`git ls-remote --tags`), поэтому **публикация тега и есть публикация версии**.

---

## Перед публикацией — локальные проверки

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy src/ttbot && pytest
```

---

## Обновить репозиторий (без нового релиза)

```bash
git add -A
git commit -m "Краткое описание изменений"
git push origin main
```

---

## Выпустить новую версию (релиз)

1. Поднять версию в [src/ttbot/__init__.py](../src/ttbot/__init__.py):
   ```python
   __version__ = "1.1.0"
   ```
2. Закоммитить и запушить:
   ```bash
   git add -A
   git commit -m "Release 1.1.0"
   git push origin main
   ```
3. Поставить числовой тег и опубликовать GitHub Release:
   ```bash
   git tag -a 1.1.0 -m "Release 1.1.0"
   git push origin 1.1.0
   gh release create 1.1.0 --title "1.1.0" --generate-notes
   ```

После этого на сервере достаточно `ttbot --update`, чтобы подтянуть релиз.

> На Windows `gh` может быть не в `PATH` — тогда вызывайте по полному пути
> `& "C:\Program Files\GitHub CLI\gh.exe" ...`.

---

## Первичное разворачивание (выполняется один раз)

```bash
# gh должен быть установлен и авторизован:
#   winget install GitHub.cli
#   gh auth login            (HTTPS)

git init -b main
git add -A
git commit -m "Initial commit"
gh repo create Ashteeer/technitium-dns-bot --public --source=. --remote=origin --push
```

> `config.yaml`, `state.json`, `*.ps1` и виртуальные окружения в репозиторий не
> попадают (см. `.gitignore`). Перед первым push проверьте `git status` на
> предмет секретов и личных данных.

---

## Установка / обновление на сервере (Ubuntu)

См. [DEPLOY.md](DEPLOY.md). Кратко:

```bash
# Установка последней версии одной командой:
curl -fsSL https://raw.githubusercontent.com/Ashteeer/technitium-dns-bot/main/INSTALL.sh | sudo bash

# Обновление / откат на конкретную версию:
sudo ttbot --update          # последняя
sudo ttbot --update 1.0.0    # конкретная (числовой тег)
ttbot --version
```
