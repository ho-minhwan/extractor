# P6 Extractor 초안 실행 흐름 안내

> **초안 상태:** 이 문서는 현재 저장소에 있는 P6 extractor 코드의 동작을 설명합니다. 아직 실제 Primavera P6 EPPM 환경에서 API, 인증, 응답 형식 또는 CDF 적재를 검증하지 않았습니다. 아래 endpoint와 필드명 등은 동작 예시를 위한 가정값입니다.

## 표시 기준

- **[현재 초안]**: 지금 코드가 실제로 수행하는 동작
- **[변경 예상]**: 실제 P6 환경 정보에 맞춰 코드나 설정이 바뀔 가능성이 높은 부분
- **[확인 필요]**: P6 관리자/환경에서 확인하거나 테스트해야 하는 정보

## 1. 전체 실행 흐름 한눈에 보기

```text
직접 실행: python -m p6_extractor
  └─ __main__.py
      ├─ P6 메트릭 객체 준비
      ├─ ExtractorService context 진입
      └─ extractor.run()
          └─ run_extractor()
              ├─ CDF Raw state store 초기화
              ├─ P6 REST Session 생성 (현재는 Basic Auth 가정)
              ├─ Project endpoint 페이지 조회 → CDF Raw
              ├─ Activity endpoint 페이지 조회 → CDF Raw
              └─ 성공 여부에 따라 state 시각 및 pipeline run 기록

Windows 서비스 실행
  └─ winservice.py
      └─ ExtractorService.run_service()
          └─ 매 분 cron 확인, 일정에 맞으면 위 run_extractor 흐름 실행
```

현재는 Project와 Activity를 **각각 독립적인 endpoint로 순차 조회**합니다. Activity 요청을 Project ID로 제한하는 연결 로직은 아직 없습니다.

## 2. 파일별 역할

| 파일 | 현재 역할 |
|---|---|
| [__main__.py](./p6_extractor/__main__.py) | CLI 진입점. 메트릭 및 service를 준비하고 한 번 실행 |
| [service.py](./p6_extractor/service.py) | Cognite extractor 래퍼와 cron 기반 반복 실행 |
| [winservice.py](./p6_extractor/winservice.py) | Windows Service 진입, 중지 이벤트 연결 |
| [config.py](./p6_extractor/config.py) | YAML 설정을 받는 dataclass 타입 정의 |
| [extractor.py](./p6_extractor/extractor.py) | P6 endpoint 호출, 페이지 처리, Raw 적재, state/pipeline 결과 관리 |
| [metrics.py](./p6_extractor/metrics.py) | P6 조회/적재 오류 및 실행시간 지표 |
| [config.yaml](./config.yaml) | CDF 연결, P6 연결, endpoint, 페이지, Raw destination 설정 예시 |
| [pyproject.toml](./pyproject.toml) | 패키지 정보 및 Python dependencies |
| [service.spec](./service.spec), [standalone.spec](./standalone.spec) | PyInstaller 빌드 설정 초안 |

## 3. 실행 진입 방식

### CLI에서 한 번 실행

[__main__.py](./p6_extractor/__main__.py)의 `main()`은 `safe_get(P6Metrics, ...)`로 메트릭을 준비한 뒤 `ExtractorService`를 context manager로 엽니다. `extractor.run()`을 직접 호출하므로 cron 시간을 기다리지 않고 한 번 실행합니다.

`ExtractorService` 생성 시 `Config`, `run_extractor`, `config.yaml` 경로와 버전을 넘깁니다. 상속한 Cognite `Extractor`가 설정을 읽고 CDF client 및 state store 등의 실행 context를 구성합니다.

### Windows 서비스에서 반복 실행

[winservice.py](./p6_extractor/winservice.py)의 `SvcDoRun()`이 `ExtractorService`를 만들고 `run_service()`를 시작합니다. `SvcStop()`은 cancellation event를 설정해 service loop에 중지 요청을 전달합니다.

[service.py](./p6_extractor/service.py)의 `run_service()`는 다음 분 경계까지 대기하고, 매 분 `schedule.schedule`을 검사합니다. 설정 예시는 `0 */12 * * *`로 12시간마다 정각에 실행하도록 되어 있습니다. cron이 일치하면 `self.run()`이 호출되어 CLI와 같은 추출 함수를 실행합니다.

서비스 레벨에서 예외가 발생하면 로그를 남기고 CDF Extraction Pipeline에 failure run을 기록합니다. 추출 본체의 대부분의 예외는 `run_extractor()` 내부에서 처리됩니다.

## 4. 설정이 코드에 연결되는 방식

[config.py](./p6_extractor/config.py)의 주요 구조는 다음과 같습니다.

- `Config`
  - `cognite`: Cognite extractor-utils의 CDF 연결 및 Extraction Pipeline 설정
  - `raw.last_run`: 실행 시각을 저장할 Raw state database/table
  - `p6`: P6 REST API 연결정보 및 데이터 query 설정
  - `metrics`: Cognite metrics 전송 설정
  - `schedule`: Windows service 일정
- `P6Config`
  - `base_url`, `user_name`, `password`, `timeout`, `query_limit`
  - `project_data`, `activity_data`
- 각 `QueryConfig`
  - `endpoint`: `base_url`에 붙이는 endpoint path
  - `raw_table`: CDF Raw database/table
  - `raw_key`: 각 record에서 Raw key로 쓸 field
  - `response_items_key`: object 응답에서 record list를 찾을 key
  - `has_more_field`: object 응답에서 다음 페이지 존재 여부를 찾을 key
  - `limit_parameter`, `offset_parameter`: query string의 페이지 인자명

[config.yaml](./config.yaml)의 `/api/project`, `/api/activity`, `ObjectId`, `items`, `hasMore`, `limit`, `offset` 값은 **실제 P6의 확인된 API 계약이 아니라 현재 초안의 예시값**입니다.

## 5. 한 번 실행될 때의 코드 흐름

### 5.1 준비 및 state store

[run_extractor()](./p6_extractor/extractor.py)는 시작 시 monotonic clock을 기록하고 extraction pipeline external ID를 가져옵니다. 이어서 설정된 Raw database/table을 사용하는 `RawStateStore`를 만들고 초기화합니다. 현재 UTC 시간을 실행 시각으로 기록합니다.

함수 signature의 `states` 인자는 현재 사용되지 않고 버려집니다. 코드 안에서 설정의 `raw.last_run`을 이용해 새 state store를 초기화합니다.

### 5.2 P6 HTTP session

현재 코드는 `requests.Session()`을 만들고 다음을 설정합니다.

- `session.auth = (user_name, password)`: Requests의 HTTP Basic 인증
- `Accept: application/json`
- 고정 User-Agent

**[변경 예상] 인증 방식이 Basic이 아니면 이 부분과 `P6Config`/환경변수 구성이 함께 바뀝니다.**

### 5.3 Project와 Activity 순차 조회

`run_extractor()`는 다음 두 설정을 고정 순서로 순회합니다.

1. `("projects", config.p6.project_data)`
2. `("activities", config.p6.activity_data)`

각 데이터 타입마다 새 `RawUploadQueue(max_queue_size=10_000)` context를 엽니다. `_extract_records()`가 endpoint 페이지를 조회하고 각 응답 record를 Raw queue에 넣습니다. queue context가 끝날 때 해당 목적지로 적재가 마무리됩니다.

**[현재 초안] Project와 Activity는 서로 독립적으로 조회됩니다.** Activity URL에 project key를 넣거나, 앞에서 받은 Project IDs를 다음 API 호출에 전달하지 않습니다. P6 endpoint가 project-scoped Activity query만 제공한다면 조회 설계를 바꿔야 합니다.

### 5.4 `_extract_records()`의 페이지 단위 처리

1. `urljoin()`으로 P6 base URL과 endpoint를 결합합니다.
2. 첫 offset은 0입니다. 설정된 limit/offset query parameter가 비어 있지 않으면 query에 `query_limit` 및 offset을 포함합니다.
3. `session.get(..., timeout=config.p6.timeout)`으로 요청하고 HTTP 오류 status는 `raise_for_status()`로 예외 처리합니다.
4. JSON 응답을 두 가지 모양으로 해석합니다.
   - JSON list: 그 list를 records로 사용합니다. offset parameter가 설정되어 있고 list 길이가 `query_limit`과 같으면 다음 페이지가 있다고 간주합니다.
   - JSON object: `response_items_key` 항목이 list여야 합니다. `has_more_field` 값의 truthiness를 이용해 다음 페이지 여부를 판단합니다.
5. 각 record가 object인지, `raw_key` field가 존재하고 null이 아닌지 검사합니다.
6. `Row(key=str(record[raw_key]), columns=record)`를 Raw queue에 넣습니다. key는 문자열로 바꾸지만 columns의 record는 원래 JSON 값을 그대로 전달합니다.
7. 다음 페이지가 있다고 판단하면 offset에 현재 페이지 record 수를 더해 반복 조회합니다.
8. HTTP, JSON 형식, key 검사 실패 시 로그/metric을 남기고 해당 데이터 타입은 실패로 표시합니다.

페이지 처리 방식은 P6 API 응답이 현재 가정과 일치해야만 올바르게 동작합니다. next-link, token/cursor, `limit/offset`이 아닌 페이지 방식은 현재 코드에서 지원하지 않습니다.

## 6. 결과, 상태 및 오류

- 두 데이터 조회가 모두 성공하면 `last_run_utc`, `last_run_epoch`를 현재 UTC 값으로 저장하고 state store를 synchronize한 다음 pipeline success run을 기록합니다.
- Project 또는 Activity 중 하나라도 실패하면 state 시각은 갱신하지 않고 pipeline failure run을 기록합니다. 다른 쪽 데이터 조회는 계속 시도합니다.
- stop event가 설정되면 현재 처리 중단을 알리고 빠져나옵니다. 이 경우 state timestamp/pipeline 최종 기록 단계까지 가지 않습니다.
- 예상하지 못한 예외는 `run_extractor()`에서 stack trace를 기록하고 pipeline failure run을 만듭니다.

**[현재 초안] state store의 이전 실행 시각은 읽거나 API 요청에 적용하지 않습니다.** 현재 시간만 저장하므로, state 기록은 있지만 증분 추출 기능은 없습니다. 매번 endpoint가 반환하는 전체 범위를 다시 조회합니다.

## 7. CDF에 저장되는 데이터

각 Project/Activity JSON object 하나가 설정된 Raw database/table에 하나의 `Row`로 들어갑니다.

- Raw database/table: 각 `project_data.raw_table`, `activity_data.raw_table`
- Raw key: 각 `raw_key` 필드의 값을 문자열로 변환
- Raw columns: 원본 JSON object
- 파일/첨부 처리: 현재 없음

Project/Activity 관계를 Raw에서 표현하는 별도 변환도 현재 없습니다. 원본 Activity 응답에 project 식별자가 들어 있다면 해당 필드는 columns에 그대로 포함될 수 있지만, 이는 API response 확인이 필요합니다.

## 8. 변경 예상 영역

| 영역 | 현재 가정/초안 | 실제 환경에 맞춰 예상되는 변경 |
|---|---|---|
| 인증 | HTTP Basic, username/password | 실제 인증에 따라 token 발급, OAuth/SSO/gateway 헤더 처리, token 갱신 및 만료 대응 |
| Base URL | 환경변수 `P6_BASE_URL` | 실제 P6 환경/버전/API context path에 맞게 수정 |
| Resource endpoints | `/api/project`, `/api/activity` | API 버전 및 P6 deployment에서 제공하는 정확한 resource path로 변경 |
| Project/Activity 관계 | 두 endpoint 독립 호출 | Activity가 project별 호출이어야 하면 project ID 순회 및 URL/query 구성 추가 |
| Pagination | `limit`/`offset`, `hasMore` 가정 | 실제 API의 page size, offset base, cursor/next link, 종료 조건에 맞게 변경 |
| Response parsing | list 또는 `{items, hasMore}` | 실제 JSON envelope, record array key, boolean/type 및 오류 응답 schema에 맞게 수정 |
| Raw keys | 둘 다 `ObjectId` 예시 | 각 resource의 안정적이고 유일한 ID field로 조정; 중복 가능성 확인 |
| Raw destinations | `p6.project`, `p6.activity` 예시 | CDF database/table naming, key 충돌 정책, 보존/정리 요구에 맞게 설정 |
| 데이터 범위 | 전체 응답을 그대로 적재 | 필터, field 선택, 제외/정규화, 관련 객체 등 요구 범위에 맞게 추가 |
| 증분/삭제 반영 | 매 실행 전체 query, 삭제 정리 없음 | 변경 시각 필터/상태 token, 전체 동기화 주기, 삭제/비활성 record 반영 정책 구현 |
| Retry/rate limit | 별도 retry adapter 없음 | P6 운영 제한, 429/일시 오류 retry/backoff, API별 timeout 요구에 맞게 보완 |
| 테스트/배포 | 로컬 실행 골격과 service spec | 실제 응답 fixture/mock 테스트, 운영 설정, Windows packaging/runtime 검증 |

## 9. 실제 테스트 전에 확인해야 할 정보

아래 항목을 P6 관리자나 실제 API 문서/응답에서 확인하면 placeholder를 실제 설정과 동작으로 바꿀 수 있습니다.

### P6 API 접속/인증

- [ ] P6 EPPM 버전과 REST API 버전
- [ ] 테스트 환경의 base URL 및 REST API context path
- [ ] 인증 방식: Basic인지, 별도 token/OAuth/SSO/gateway인지
- [ ] 인증에 필요한 username/password, client credentials, token URL/scope 또는 추가 header
- [ ] TLS 인증서, proxy, VPN, IP allowlist 등 네트워크 조건

### Project와 Activity API 계약

- [ ] Project 목록 endpoint와 실제 response 예시
- [ ] Activity 목록 endpoint와 실제 response 예시
- [ ] Activity를 전체에서 조회할 수 있는지, project ID별로 조회해야 하는지
- [ ] 필요한 project/activity 범위와 필터 조건
- [ ] 각 응답의 안정적인 unique key field
- [ ] Raw에 적재할 field 전체/부분 및 민감정보 제외 조건

### Pagination 및 호출 동작

- [ ] 페이지 크기 parameter와 최대 허용 크기
- [ ] offset/page indexing이 0부터인지 1부터인지
- [ ] 다음 페이지 여부가 어떤 field/type으로 제공되는지
- [ ] next link, cursor/token 기반인지, limit/offset 기반인지
- [ ] 조회량 제한, rate limit, timeout, 재시도 정책

### CDF 적재 및 운영 정책

- [ ] 실제 Raw database/table 이름과 key 정책
- [ ] 변경분만 가져와야 하는지, 증분 기준 field 또는 watermark
- [ ] P6에서 삭제/취소된 Project/Activity를 CDF에서 어떻게 반영할지
- [ ] schedule과 초기 full load 허용 시간/예상 데이터 건수
- [ ] Extraction Pipeline 및 metrics dataset 설정값
- [ ] 운영 OS와 Windows Service/CLI 중 배포 방식

## 10. 내일 테스트할 때 추천하는 순서

1. 먼저 인증과 네트워크만 확인합니다. 실제 endpoint 하나에 호출해 status code 및 인증 실패 원인을 확인합니다.
2. Project endpoint 응답 JSON을 확인해 목록 위치, key, 페이지 정보를 기록합니다.
3. Activity endpoint도 같은 정보를 확인하고 project 범위 지정이 필요한지 확인합니다.
4. config의 endpoint, pagination, response key, Raw key/table을 확인된 값으로 수정합니다.
5. 작은 페이지 크기와 테스트용 Raw destination으로 한 번 실행해 적재된 row key/columns를 확인합니다.
6. 다음 page에서 중복이나 누락이 없는지 점검하고, 실패·timeout·stop 동작을 확인합니다.
7. full/incremental 및 삭제 반영 정책을 합의한 다음 운영 schedule과 대량 실행을 결정합니다.

테스트 중 credentials나 token은 로그/문서/commit에 복사하지 말고 환경변수 또는 승인된 secret store로 관리합니다.

## 11. 코드 읽기 순서

1. [__main__.py](./p6_extractor/__main__.py): 한 번 실행하는 진입점
2. [config.py](./p6_extractor/config.py)와 [config.yaml](./config.yaml): 필드가 코드에 어떻게 전달되는지
3. [service.py](./p6_extractor/service.py): Windows service scheduling
4. [extractor.py](./p6_extractor/extractor.py)의 `run_extractor()`: Project/Activity를 어떤 순서로 실행하는지
5. 같은 파일의 `_extract_records()`: HTTP, pagination, JSON 검사, Raw row 구성
6. [metrics.py](./p6_extractor/metrics.py): 실행 중 어떤 지표가 증가하는지
7. [winservice.py](./p6_extractor/winservice.py): Windows Service 시작/중지 흐름

실제 P6 API를 확인한 뒤에는 **[확인 필요] 체크리스트 → [변경 예상] 표 → config 수정 및 필요한 코드 변경** 순으로 이 문서를 업데이트하면 초안과 운영 구현의 차이를 추적하기 쉽습니다.
