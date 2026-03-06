# subtitle-fetcher

Python CLI tool to search OpenSubtitles, show multiple subtitle results, let you select one, download it, extract ZIP files if needed, and save the subtitle as:

`movie_name.en.srt`

## Project structure

```text
subtitle-fetcher/
  subtitle.py
  requirements.txt
  README.md
```

## Requirements

- Python 3.10+
- OpenSubtitles API key

## Setup

```bash
pip install -r requirements.txt
```

Set your API key:

```bash
export OPENSUBTITLES_API_KEY="your_api_key_here"
```

## Usage

```bash
python subtitle.py "movie name"
```

Example:

```bash
python subtitle.py "The Matrix"
```

This shows top results and prompts for selection. After selection, the subtitle is downloaded automatically.

### Automatic mode

Skip the prompt and auto-download the top result:

```bash
python subtitle.py "The Matrix" --auto
```

Show more candidates (default is 5):

```bash
python subtitle.py "The Matrix" --max-results 10
```

On success, output file will be created in the current directory:

```text
the_matrix.en.srt
```

## Error handling

The CLI reports clear errors for common issues:

- Missing API key
- Network/API failures
- Rate limits
- No subtitles found
- Invalid or empty downloaded files
- ZIP archives without `.srt` files
