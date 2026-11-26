# story-cut

게임/방송 영상을 보고 **원하는 부분만 남긴 편집 초안**을 만들어 줍니다. 초안은 [OpenReel](https://github.com/Firstund/openreel-video)에서 바로 가져와 다듬을 수 있는 `openreel-edit` JSON입니다.

- **편집 모드 5가지**: 스토리(기본), 하이라이트, 전투만, 무음 컷(토크 압축), 쇼츠(세로 60초). 말로 적어도 됩니다. 예: `"보스전 하이라이트 3분"`
- **휴대폰에서 사용**: 무거운 분석은 PC가 하고, 폰 브라우저로 영상 고르기 → 모드/요청 → 진행 상황 → 결과 확인·다듬기 → MP4 받기까지 할 수 있습니다. ([휴대폰에서 쓰기](#휴대폰에서-쓰기))
- **PC에서 바로 MP4**: 초안대로 컷/자막/세로 자르기를 ffmpeg로 바로 만듭니다.

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

## 이미 설치했다면: 업데이트 방법

PowerShell을 열고 아래를 한 줄씩 입력합니다.

```powershell
cd D:\_ForBroadcast\StroyCut\story-cut
git pull
.\.venv\Scripts\activate
python -m pip install -e .
story-cut --version
```

`story-cut 0.2.1` 이상이 나오면 됩니다. (새 기능에 필요한 웹 서버 부품도 이때 같이 설치됩니다.)

## 편집 모드

| 모드 | 이름 | 하는 일 | AI 모델 |
|------|------|---------|---------|
| `story` | 스토리 (기본) | 컷신과 게임 속 대사만 남깁니다. 예전과 똑같이 동작합니다. | 필요 |
| `highlight` | 하이라이트 | 전투 + 소리가 크게 터진 순간(리액션, 함성)을 남깁니다. | 필요 |
| `combat` | 전투만 | 전투 장면을 모두 남깁니다. | 필요 |
| `talk` | 무음 컷 | 말소리가 없는 구간을 잘라 냅니다. 토크/잡담/튜토리얼 방송용. | **불필요** (빠름) |
| `shorts` | 쇼츠 | 가장 볼만한 순간만 골라 **9:16 세로, 58초 이내**로 만듭니다. | 필요 |

```powershell
story-cut analyze ..\test20.mp4 --out highlight.json --mode highlight --backend lmstudio --model qwen/qwen3-vl-8b --language ko
story-cut analyze ..\test20.mp4 --out shorts.json --mode shorts --backend lmstudio --model qwen/qwen3-vl-8b --language ko
story-cut analyze ..\test20.mp4 --out talk.json --mode talk --language ko
story-cut modes
```

**말로 요청하기**: `--prompt`에 원하는 편집을 적으면, LM Studio의 모델이 읽고 모드·남길 장면·길이를 정합니다. (모델이 꺼져 있으면 단어로 추측합니다.)

```powershell
story-cut analyze ..\test20.mp4 --out edit.json --prompt "보스전 위주로 3분 하이라이트 만들어줘" --backend lmstudio --model qwen/qwen3-vl-8b --language ko
story-cut analyze ..\test20.mp4 --out edit.json --prompt "스토리랑 전투 같이, 10분으로" --backend lmstudio --model qwen/qwen3-vl-8b --language ko
```

- `--mode`와 `--prompt`를 같이 주면 모드는 `--mode`를 따르고, 길이·추가 장면은 요청에서 가져옵니다.
- **AI 분류 결과는 모드끼리 공유됩니다.** 같은 영상을 스토리로 한 번 돌렸다면, 하이라이트나 쇼츠로 다시 돌릴 때는 AI 분류를 다시 하지 않아 금방 끝납니다.
- 결과 길이를 정하고 싶으면 `--max-total 600`(초)처럼 줍니다. 넘치면 점수가 높은 구간만 남깁니다.

## PC에서 바로 MP4 만들기

초안을 OpenReel에서 열지 않고 바로 영상으로 만듭니다. (컷, 자막, 세로 자르기만 반영. OpenReel에서 넣은 효과/전환은 OpenReel의 Export를 쓰세요.)

```powershell
story-cut render shorts.json ..\test20.mp4 --out shorts.mp4 --burn-subs
```

`--burn-subs`는 자막을 영상에 새겨 넣습니다. 자막 파일(`.srt`)은 항상 옆에 따로 저장됩니다. AMD 그래픽카드로 빠르게 만들려면 `--encoder h264_amf`를 붙여 보세요.

## 휴대폰에서 쓰기

무거운 일(장면 나누기, 받아쓰기, AI 분류, 영상 만들기)은 **집 PC**가 하고, 휴대폰은 브라우저로 PC에 접속해 조작만 합니다. 앱 설치는 필요 없습니다.

### 준비 (PC에서 한 번)

1. 위의 **업데이트 방법**대로 story-cut을 최신으로 만듭니다.
2. OpenReel도 최신으로 받습니다. ([OpenReel 쪽 안내](https://github.com/Firstund/openreel-video#휴대폰에서-쓰기-story-cut-연동))

```powershell
cd D:\_ForBroadcast\openreel-video
git pull
pnpm install
```

### 켜기 (쓸 때마다, PowerShell 창 2개)

**창 1 — OpenReel (휴대폰 접속용)**

```powershell
cd D:\_ForBroadcast\openreel-video
pnpm dev:remote
```

**창 2 — story-cut 웹 서버**

```powershell
cd D:\_ForBroadcast\StroyCut\story-cut
.\.venv\Scripts\activate
story-cut serve --videos D:\_ForBroadcast\StroyCut
```

- `--videos`에는 영상이 있는 폴더를 넣습니다. 하위 폴더 3단계까지 영상 목록에 나옵니다.
- LM Studio도 켜 두세요 (Developer 탭 서버 Running, 모델 `qwen/qwen3-vl-8b` 로드).
- 처음 켜면 Windows 방화벽 창이 뜰 수 있습니다. **개인 네트워크**에 체크하고 **허용**을 누르세요.
- 두 창 모두 켜 둔 채로 써야 합니다. 끌 때는 창을 X로 닫으면 됩니다.
- 창 1은 처음 켤 때와 업데이트 직후에 **폰용 화면을 빌드하느라 1~3분** 걸립니다. `휴대폰 접속` 줄이 나올 때까지 기다리세요.
- 주소 목록에 **`공인 IP ⚠`** 가 보이면 PC가 인터넷에 바로 연결된 상태입니다. 비밀번호가 있어야 열리지만, 그 주소는 남에게 알려 주지 말고 집 밖에서는 Tailscale 주소를 쓰세요.

### 같은 와이파이에서 폰으로 열기

1. 창 1(OpenReel)에 이런 줄이 나옵니다: `같은 와이파이  https://192.168.0.12:5173/storycut/?token=AbCdEf...`
   창 2(story-cut)에도 같은 주소와 QR 코드가 나옵니다.
2. 폰 카메라로 QR을 찍거나, 그 주소를 폰 크롬/사파리에 그대로 입력합니다.
3. **"연결이 비공개로 설정되어 있지 않습니다"** 경고가 나오면 **고급 → 192.168...(으)로 이동(안전하지 않음)** 을 누릅니다. (PC가 스스로 만든 인증서라서 나오는 경고입니다. 처음 한 번만.)
4. story-cut 화면이 뜹니다. 주소에 비밀번호(token)가 들어 있어 자동으로 로그인됩니다. 비밀번호를 물으면 PC 창의 **접속 비밀번호**를 넣으세요.
5. 홈 화면에 추가해 두면 앱처럼 쓸 수 있습니다.

### 집 밖에서 열기 (Tailscale, 무료)

[Tailscale](https://tailscale.com)은 내 기기끼리만 연결되는 비공개 네트워크입니다. 공유기 설정(포트 열기)이 필요 없고, 인터넷에 PC를 공개하지 않습니다.

1. PC: <https://tailscale.com/download> 에서 Windows용을 설치하고 구글 계정 등으로 로그인합니다.
2. 폰: 앱스토어/플레이스토어에서 **Tailscale** 앱을 설치하고 **같은 계정**으로 로그인한 뒤 연결(Connect)을 켭니다.
3. PC에서 위의 창 1, 창 2를 켜면 `Tailscale  https://100.x.y.z:5173/storycut/?token=...` 줄이 나옵니다. (안 나오면 PC 작업표시줄 Tailscale 아이콘에서 내 PC의 100.으로 시작하는 주소를 확인하세요.)
4. 폰에서 그 주소를 엽니다. 경고 화면은 같은 방법으로 넘어갑니다.
5. 다 쓰면 폰의 Tailscale 연결은 꺼도 됩니다.

### 폰 화면에서 하는 일

1. **영상 고르기**: PC 폴더의 영상 목록에서 고르거나, **⬆ 올리기**로 폰의 영상을 PC로 보냅니다.
2. **모드 고르기 / 요청 적기**: 스토리·하이라이트·전투만·무음 컷·쇼츠 중에서 고르거나, "말로 설명"을 누르고 요청을 적습니다.
3. **분석 시작**: 단계별 진행 막대가 나옵니다. 폰 화면을 꺼도 PC에서 계속 돌아갑니다.
4. **결과 확인**: 위쪽 플레이어의 **▶ 남긴 부분 이어보기**로 결과를 미리 봅니다. 각 클립은 ✅로 빼거나 넣고, **◀ 시작 / 끝 ▶** 버튼으로 0.5초씩 늘리거나 줄입니다. 다 고쳤으면 **변경 저장**.
   - 밖에서 데이터가 아까우면 **가벼운 화질로 미리보기**를 켭니다. 540p 사본은 분석이 끝나면 PC가 자동으로 만들어 둡니다 (20분 영상 ≈ 70MB).
5. **내보내기**
   - **PC에서 MP4 만들기 (원본 화질)** 또는 **폰용 작게 (720p)**: PC가 MP4를 만듭니다. 진행률이 위쪽 카드와 아래 막대에 보이고, 화면을 꺼도 PC에서 계속 만듭니다. (쇼츠는 자막 넣기가 기본으로 켜져 있음)
   - 다 되면 **✅ 영상 완성!** 카드가 뜨고 폰이 짧게 진동합니다. 카드의 **⬇ 폰에 저장 (크기)** 를 누르면 폰에 받아집니다.
     - 안드로이드(크롬): 알림창/`다운로드` 앱(파일 앱의 Download 폴더)에 생깁니다. 안 되면 **▶ 새 탭에서 보기** → 오른쪽 위 ⋮ → 다운로드.
     - 아이폰(사파리): **공유 / 사진 앱에 저장** → **비디오 저장**. 또는 새 탭에서 열고 공유 버튼.
     - 원본 화질 파일은 클 수 있습니다 (20분 1080p면 수백 MB~1GB 이상). 폰에서 빨리 보고 올리기만 할 거면 **폰용 작게 (720p)** 가 훨씬 작고 빠릅니다.
   - **OpenReel (폰용 가벼운 영상)** → 폰에서 OpenReel 편집기로 바로 열립니다. 여는 동안 `1/3 초안 받는 중 → 2/3 영상 받는 중 (N / 68 MB) → 3/3 여는 중`이 보입니다 (20분 영상, 와이파이 기준 약 15초). 더 다듬은 뒤 오른쪽 아래 **story-cut으로 보내기**를 누르고, story-cut 화면에서 **PC에서 MP4 만들기**를 누르면 원본 화질로 만들어집니다.
   - **JSON 받기**: 초안 파일을 받습니다.
   - **다른 모드로 다시**: AI 분류가 저장돼 있어 빨리 끝납니다.

결과 파일은 PC의 `영상 폴더\story-cut-results\작업번호\`에 저장됩니다.

### 비밀번호

- 처음 켤 때 자동으로 만들어져 `C:\Users\내이름\.story-cut\token`에 저장됩니다. OpenReel(`pnpm dev:remote`)과 story-cut이 같은 비밀번호를 씁니다.
- 바꾸려면 `story-cut serve --videos ... --token 새비밀번호` 로 한 번 켜면 됩니다. (OpenReel 창도 다시 켜세요.)
- 비밀번호가 들어간 주소는 다른 사람에게 보내지 마세요.

### OpenReel 없이 story-cut만 폰에서 쓰기

```powershell
story-cut serve --videos D:\_ForBroadcast\StroyCut --host 0.0.0.0
```

창에 나온 `http://192.168...:8765/?token=...` 주소로 엽니다. 결과 확인·다듬기·MP4 만들기는 다 되지만, 폰에서 OpenReel 편집기로 여는 기능은 `pnpm dev:remote`가 켜져 있어야 합니다.

## OpenReel에서 초안 열기

1. OpenReel 웹 또는 데스크톱에서 새 프로젝트를 엽니다.
2. **원본 영상 파일**을 미디어 보관함에 넣습니다. (JSON만으로는 영상이 따라가지 않습니다. 파일 이름과 크기로 다시 연결됩니다.)
3. AI 편집 쪽의 **Import edit JSON**으로 `edit.json`을 가져옵니다.
4. 보고서와 타임라인의 마커를 보고, 잘못된 구간을 고칩니다.

이미 만든 `edit.json`을 다시 분석하지 않고 정리만 하려면:

```powershell
story-cut fix-json edit.json edit-fixed.json
```

OpenReel이 `metadata.sourceColor: unsupported property` 같은 오류를 내면 [Firstund/openreel-video](https://github.com/Firstund/openreel-video) 최신 버전으로 `git pull` 하세요. (영상 연결 뒤 생기던 이 오류는 고쳐졌습니다.)

`story-cut serve`를 쓰면 이 과정이 필요 없습니다. 결과 화면의 **OpenReel에서 열기**를 누르면 초안과 영상이 자동으로 들어갑니다.

## 자주 쓰는 옵션

| 옵션 | 의미 |
|------|------|
| `--backend lmstudio` | 기본. 로컬 OpenAI 호환 서버 (LM Studio) |
| `--backend mock` | 모델 없이 테스트용 순환 라벨 |
| `--backend gemini` | 선택. Google Gemini API (`GEMINI_API_KEY` 필요) |
| `--endpoint http://localhost:1234/v1` | 로컬 서버 주소 |
| `--model 이름` | LM Studio에 보이는 모델 이름 (예: `qwen/qwen3-vl-8b`) |
| `--mode story` | 편집 모드: `story`, `highlight`, `combat`, `talk`, `shorts` |
| `--prompt "요청"` | 원하는 편집을 말로 적기 |
| `--max-total 600` | 결과 최대 길이(초) |
| `--aspect 9:16` | 화면 비율 (`source`, `9:16`, `1:1`, `16:9`) |
| `--silence-db -35` | 무음 컷에서 무음으로 볼 소리 크기. 배경음이 크면 `-30` |
| `--keep cutscene,dialogue` | 남길 라벨. 전투도 남기려면 `cutscene,dialogue,combat` |
| `--pad 0.25` | 남긴 구간 앞뒤 여유(초). 아래 숫자 옵션은 안 주면 모드 기본값 |
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
