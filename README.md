# story-cut

게임 플레이 영상을 보고 **스토리 구간만 남긴 편집 초안**을 만들어 줍니다. 초안은 [OpenReel](https://github.com/Firstund/openreel-video)에서 바로 가져와 다듬을 수 있는 `openreel-edit` JSON입니다.

이 프로그램은 **내 PC에서** 돌아갑니다. 영상을 클라우드에 올리지 않습니다. 구간이 컷신인지, 대사인지, 전투인지를 판단할 때는 기본적으로 [LM Studio](https://lmstudio.ai)에 받아 둔 **Qwen3-VL 8B** 같은 로컬 비전 모델을 씁니다. (NVIDIA CUDA가 없어도 됩니다. AMD 그래픽카드에서도 LM Studio로 모델을 돌릴 수 있습니다.)

자동으로 완성본이 나오지는 않습니다. 사람이 보고서와 타임라인을 보고 고치는 것이 전제입니다.

## 준비물

윈도우 PC, Python 3.10 이상, ffmpeg. 그래픽카드는 AMD Radeon RX 7900 GRE 16GB처럼 메모리 16GB 정도면 8B 비전 모델을 로컬로 쓰기에 충분합니다.

### 1. Python 설치

1. [python.org](https://www.python.org/downloads/)에서 Windows용 설치 파일을 받습니다.
2. 설치할 때 **Add python.exe to PATH** 칸을 꼭 체크합니다.
3. 새 명령 프롬프트(또는 PowerShell)를 열고 아래를 입력해 버전이 3.10 이상인지 확인합니다.

```powershell
python --version
```

### 2. ffmpeg 설치

영상에서 장면을 나누고 프레임을 뽑을 때 필요합니다.

1. [ffmpeg 다운로드](https://www.gyan.dev/ffmpeg/builds/)에서 `ffmpeg-release-essentials.zip`을 받습니다.
2. 압축을 풀고, 그 안의 `bin` 폴더(안에 `ffmpeg.exe`가 있는 폴더)를 기억합니다. 예: `C:\ffmpeg\bin`
3. 윈도우 검색에서 **환경 변수**를 열고, 사용자 변수 `Path`에 그 `bin` 폴더를 추가합니다.
4. 명령 창을 **새로** 연 다음 확인합니다.

```powershell
ffmpeg -version
```

### 3. 이 프로그램 설치

```powershell
git clone https://github.com/Firstund/story-cut.git
cd story-cut
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e .
```

`git`이 없다면 깃허브 페이지에서 초록색 **Code → Download ZIP**으로 받아 압축을 풀어도 됩니다.

### 4. LM Studio로 로컬 AI 켜기 (실제 분류를 할 때)

테스트만 할 때는 이 단계를 건너뛰고 아래의 `--backend mock`을 쓰면 됩니다.

1. [LM Studio](https://lmstudio.ai)를 설치합니다.
2. 앱에서 `Qwen3-VL 8B` (또는 비슷한 Qwen3-VL 8B 양자화 모델, 예: Q4)를 검색해 받습니다.
3. **Local Server** (로컬 서버)를 포트 `1234`로 시작합니다. 주소는 보통 `http://localhost:1234`입니다.
4. 서버 화면에 보이는 **모델 이름**을 복사합니다. `qwen/qwen3-vl-8b`처럼 기본값과 다르면 실행할 때 `--model`에 그 이름을 그대로 넣습니다.

16GB 메모리에서 모델이 안 올라가면, 더 작은 양자화(Q4) 버전을 받아 보세요.

## 한 번 돌려 보기

먼저 테스트용 짧은 영상을 만들고, 로컬 AI 없이 **mock** 백엔드로 파이프라인이 도는지 확인합니다.

```powershell
.\.venv\Scripts\activate
story-cut sample --out sample.mp4
story-cut analyze sample.mp4 --out sample-edit.json --backend mock --whisper-model tiny
```

잠깐 기다리면 다음 파일이 생깁니다.

- `sample-edit.json` — OpenReel에 가져갈 편집 초안
- `sample-edit.report.md` — 사람이 읽기 쉬운 구간 보고서
- `sample-edit.report.csv` — 같은 내용을 표로 본 파일 (엑셀에서 열 수 있음)

실제 게임 영상을 분류하려면 LM Studio를 켠 다음:

```powershell
story-cut analyze "C:\Videos\gameplay.mp4" --out edit.json --backend lmstudio --model qwen/qwen3-vl-8b --language ko
```

한국어 게임 방송이면 `--language ko`를 넣는 것이 좋습니다. 받아쓰기가 느리면 `--whisper-model base` 또는 `tiny`를 쓰세요. 더 정확하게는 기본값 `small`을 유지하세요. AMD 그래픽카드에서는 받아쓰기를 CPU로 돌립니다(`--device cpu`, 기본값).

중단했다가 이어서 하려면, 같은 명령만 다시 실행하면 됩니다. 장면 나누기와 받아쓰기는 캐시(기본: 결과 파일 옆 `.story-cut-cache`)에서 불러옵니다. 분류 규칙이 바뀐 버전으로 다시 돌리면 구간 분류만 처음부터 다시 합니다.

## OpenReel에서 초안 열기

1. OpenReel 웹 또는 데스크톱에서 새 프로젝트를 엽니다.
2. **원본 영상 파일**을 미디어 보관함에 넣습니다. (JSON만으로는 영상이 따라가지 않습니다. 파일 이름과 크기로 다시 연결됩니다.)
3. AI 편집 쪽의 **Import edit JSON**으로 `edit.json`을 가져옵니다.
4. 보고서와 타임라인의 마커를 보고, 잘못된 구간을 고칩니다.

## 자주 쓰는 옵션

| 옵션 | 의미 |
|------|------|
| `--backend lmstudio` | 기본. 로컬 OpenAI 호환 서버 (LM Studio) |
| `--backend mock` | 모델 없이 테스트용 순환 라벨 |
| `--backend gemini` | 선택. Google Gemini API (`GEMINI_API_KEY` 필요) |
| `--endpoint http://localhost:1234/v1` | 로컬 서버 주소 |
| `--model 이름` | LM Studio에 보이는 모델 이름 (예: `qwen/qwen3-vl-8b`) |
| `--keep cutscene,dialogue` | 남길 라벨. 전투도 남기려면 `cutscene,dialogue,combat` |
| `--pad 0.25` | 남긴 구간 앞뒤 여유(초) |
| `--min-shot 1.5` | 이보다 짧은 샷은 이웃과 합칩니다 |
| `--min-keep 2` | 이보다 짧은 남긴 조각은 버립니다 (전투 이펙트 등) |
| `--merge-gap 1` | 이 간격 안의 남긴 구간은 하나로 합칩니다 |
| `--max-segment 20` | 한 구간의 최대 길이(초) |
| `--ocr` | 대사창 글자 읽기 (별도 설치, 기본은 꺼짐) |
| `--skip-transcribe` | 받아쓰기 생략 |
| `--language ko` | 받아쓰기 언어 고정. 한국어 방송에 권장. 기본 `auto` |

가능한 라벨: `cutscene`(컷신), `dialogue`(대사), `combat`(전투), `exploration`(탐험), `menu`(메뉴), `other`(기타).

## Gemini를 쓰고 싶을 때

기본값이 아닙니다. [Google AI Studio](https://aistudio.google.com/)에서 API 키를 만든 다음:

```powershell
$env:GEMINI_API_KEY = "여기에-키"
story-cut analyze gameplay.mp4 --out edit.json --backend gemini --model gemini-2.0-flash
```

이 경우에만 구간 이미지와 대사 일부가 Google 서버로 전송됩니다.

## 대사창 OCR (선택)

음성 없는 컷신의 글자를 읽으려면:

```powershell
python -m pip install -r requirements-ocr.txt
story-cut analyze gameplay.mp4 --out edit.json --ocr
```

게임 폰트는 잘 안 읽힐 수 있습니다. 기본은 꺼 두었습니다.

## 개발자용: OpenReel JSON이 맞는지 확인

이 저장소와 나란히 OpenReel 소스가 있고 의존성을 설치했다면:

```powershell
node scripts/check-openreel-edit.mjs sample-edit.json --openreel ..\openreel-video
```

OpenReel의 `parsePortableEdit`로 초안을 검사합니다.

## 한계

- "스토리 위주로 편집해 줘"를 한 문장으로 끝내 주지는 않습니다. AI가 초안을 짜면 사람이 검수합니다.
- 로컬 8B 비전 모델의 분류는 틀릴 수 있습니다. 보고서의 이유와 신뢰도를 꼭 보세요.
- 방송 영상에서는 진행자의 말과 게임 대사가 한 받아쓰기에 섞입니다. 프로그램은 화면을 보고 구분하려고 하지만, 진행자가 길게 말하는 구간을 스토리로 오인할 수 있습니다.
- 튜토리얼, 채팅창, 전투 스킬 연출이 대사·컷신으로 잡히기도 합니다.
- 3시간짜리 원본은 받아쓰기와 분류에 시간이 꽤 걸립니다. 캐시로 이어서 할 수 있습니다.
- 장면이 부드럽게 넘어가면 샷이 덜 잘릴 수 있습니다.
- OpenReel 가져오기는 원본 파일을 따로 연결해야 합니다.
- 받아쓰기는 AMD에서 CPU(int8)가 기본입니다. 속도는 그래픽카드 가속보다 느립니다. 게임 고유 명사는 잘못 적힐 수 있습니다.

## 앞으로 할 일

- 구간 분류를 여러 개 동시에 돌리기
- OpenReel 안에서 "초안 가져오기" 버튼으로 더 쉽게 연결하기
- 한글 대사·게임 UI에 맞춘 프롬프트 다듬기
- 분류 결과를 사람이 고치면 다시 내보내는 왕복 흐름

## 라이선스

MIT
