# SCM Extractor 실행 흐름 안내

이 문서는 현재 SCM extractor 코드가 **어떤 순서로 실행되고, SCM 응답이 어떤 형태로 CDF에 적재되는지** 설명합니다. 소스 코드 설명을 위한 문서이며, 구현의 빈틈이나 동작상 주의점도 구분해 기록했습니다.

## 1. 먼저 보는 전체 흐름

```text
직접 실행
  __main__.py
    └─ ExtractorService 생성 및 context 진입
         └─ extractor.run()
              └─ run_extractor()

Windows 서비스 실행
  winservice.py
    └─ ExtractorService 생성 및 context 진입
         └─ extractor.run_service()
              └─ cron schedule에 맞으면 extractor.run()
                   └─ run_extractor()

run_extractor()
  ├─ RawStateStore 초기화 및 마지막 실행 시각 읽기
  ├─ Supplier → attachment/site
  ├─ Purchase Order → lines → DFF / attachments
  ├─ RFQ → lines → DFF / attachments
  └─ 성공 여부에 따라 state 갱신 및 Extraction Pipeline run 기록
```

추출 작업의 실질적인 코드는 [extractor.py](./scm_extractor/extractor.py)에 있습니다. `__main__.py`와 `service.py`는 실행 환경을 준비하고, `run_extractor()`가 세 데이터 영역을 차례대로 호출합니다.

## 2. 파일별 책임

| 파일 | 역할 |
|---|---|
| [__main__.py](./scm_extractor/__main__.py) | 한 번 실행하는 CLI 진입점. 메트릭을 준비하고 `ExtractorService`를 열어 `extractor.run()` 호출 |
| [service.py](./scm_extractor/service.py) | 공통 Extractor 래퍼. 기본 일정 확인, 예약 실행, 실패 시 Extraction Pipeline에 실패 기록 |
| [winservice.py](./scm_extractor/winservice.py) | Windows Service 진입점. SCM service lifecycle과 중지 이벤트를 연결 |
| [config.py](./scm_extractor/config.py) | YAML 설정을 Python dataclass 구조로 해석하는 타입 정의 |
| [extractor.py](./scm_extractor/extractor.py) | SCM REST API 호출, 레코드 변환, Raw/Files 업로드, 상태 및 실행 결과 관리 |
| [metrics.py](./scm_extractor/metrics.py) | SCM 전용 Prometheus 메트릭 정의 |
| [config.yaml](./config.yaml) | 실행 설정 파일. 현재 저장소에 있는 파일은 `type: remote`와 CDF 연결 정보 및 pipeline ID만 포함 |
| [pyproject.toml](./pyproject.toml) | Python 버전과 의존성 정의 |

## 3. 실행 시작 지점

### CLI로 한 번 실행

[__main__.py](./scm_extractor/__main__.py)의 `main()`은 다음을 합니다.

1. `safe_get(SCMMetrics, ...)`로 SCM 메트릭 객체를 준비합니다.
2. `ExtractorService`를 context manager로 생성합니다. 이때 extractor 이름, 설정 타입(`Config`), 실행 함수(`run_extractor`), 설정 파일 경로(`config.yaml`)를 전달합니다.
3. `extractor.run()`을 바로 호출합니다. 따라서 CLI는 cron을 기다리지 않고 한 번 실행합니다.

`ExtractorService`가 context manager여야 `run()`을 쓸 수 있습니다. 서비스의 `run()`은 이 조건을 검사한 뒤 등록된 `run_handle`에 Cognite client, state store, config, cancellation event를 넘깁니다.

### Windows Service로 반복 실행

[winservice.py](./scm_extractor/winservice.py)는 Windows Service Manager의 시작/중지 이벤트를 받습니다. `SvcDoRun()`에서 `ExtractorService`를 만들고 `run_service()`를 실행하며, `SvcStop()`은 cancellation event를 설정합니다.

[service.py](./scm_extractor/service.py)의 `run_service()`는 다음 분 단위 경계까지 기다린 뒤 반복합니다. 매 분마다 `_schedule_condition()`이 cron 식을 확인하고, 일정이 맞으면 `self.run()`을 호출합니다. `ScheduleConfig` 기본 일정은 `0 */12 * * *`(12시간마다 정각)입니다. 실제 설정에 `schedule`이 있으면 설정값이 적용됩니다.

예약 실행 중 예외가 밖으로 전파되면 서비스 wrapper가 로그를 남기고 Extraction Pipeline에 failure run을 기록합니다. 정상적인 추출 내부 오류는 대개 `run_extractor()`가 처리하므로, service wrapper까지 올라오지 않습니다.

## 4. 설정이 코드에 연결되는 방식

[config.py](./scm_extractor/config.py)는 아래 구조를 기대합니다.

- 최상위 `Config`
  - `statestore`: 마지막 실행 시각을 보관할 Raw database/table
  - `metrics`: Cognite 메트릭 전송 설정
  - `schedule`: Windows 서비스의 cron 일정
  - `scm`: SCM 계정, 페이지 크기, timeout, 파일 인스턴스 space 및 데이터 query 설정
- `SCMConfig`
  - `supplier_data`
  - `purchase_order_data`
  - `request_for_quotation_data`
- 각 `QueryConfig`
  - `url`: `.format(...)`으로 경로 변수를 채우는 API URL
  - `raw_table`: 적재할 Raw database/table
  - `raw_key`: Raw row key 또는 복합 key 구성에 쓰는 필드명
  - `omit_fields`: 원본에서 Raw columns로 복사하지 않을 필드 목록

각 데이터 그룹 안에서 코드가 참조하는 query 이름은 `supplier_query`, `attachment_query`, `site_query`, `purchase_order_query`, `lines_query`, `DFF_query`, `request_for_quotation_query` 등입니다. 실제 URL·테이블·key는 이 query 설정들과 일치해야 합니다.

주의: 저장소의 [config.yaml](./config.yaml)은 `type: remote`이며, 이 파일 자체에는 위의 `scm`, `statestore`, `schedule`, `metrics` 값을 모두 담고 있지 않습니다. 따라서 배포 시 사용되는 **실효 설정(effective config)**을 별도로 확인해야 endpoint, Raw 테이블, key, query limit 등의 운영값을 알 수 있습니다. 코드의 `Config` 정의만으로 실제 SCM URL을 추정할 수는 없습니다.

## 5. 공통 데이터 처리 패턴

세 추출 함수는 대체로 같은 방식을 반복합니다.

1. `requests.Session`을 생성하고 사용자 이름/비밀번호를 `session.auth`에 설정합니다. 이는 Requests의 HTTP Basic 인증 패턴입니다.
2. HTTPS 요청에 `HTTPAdapter`와 `Retry`를 적용합니다. GET 요청에서 429, 500, 502, 503, 504 응답을 최대 3회 재시도하도록 설정합니다.
3. 설정 URL에 limit/offset 또는 parent ID를 채워 `GET`합니다. 대부분 `timeout=config.scm.timeout`을 전달합니다.
4. `raise_for_status()`로 HTTP 오류를 예외로 만들고, JSON에서 `hasMore`와 `items`를 읽습니다.
5. `hasMore`가 참인 동안 offset을 `query_limit`만큼 증가시켜 다음 페이지를 요청합니다.
6. `omit_fields`에 지정된 필드를 제외하고 `Row(key=..., columns=...)`를 `RawUploadQueue`에 넣습니다.
7. 오류는 함수별 `function_succeed=False`로 표시하고, 다음 데이터/parent 처리를 계속할 수 있는 경우 계속합니다.

각 데이터 그룹의 `RawUploadQueue`는 최대 10,000개, 파일 `BytesUploadQueue`는 최대 1,000개로 생성됩니다. queue context를 나가면서 queue에 쌓인 작업이 Cognite에 업로드됩니다.

## 6. 한 번 실행될 때의 실제 데이터 순서

### 6.1 시작 및 상태 저장소

[run_extractor()](./scm_extractor/extractor.py)는 시작 시각을 기록하고, CDF extraction pipeline external ID를 읽습니다. 그다음 SCM 메트릭을 가져오고 `create_state_store()`를 호출합니다.

`create_state_store()`는 설정된 Raw database/table을 대상으로 `RawStateStore`를 만들고 초기화한 뒤 `last_run_utc`, `last_run_epoch` 값을 읽습니다. 현재 실행 시각은 UTC로 별도 기록합니다.

이 두 이전 시각은 추출 함수 인자로 전달되지만, 현재 SCM 추출 함수 내부에서 사용되지 않습니다. 따라서 이 코드만 보면 **증분 조회가 아니라 매 실행마다 전체 endpoint를 조회**합니다. 이름과 docstring은 마지막 실행 시각을 증분 기준처럼 보이게 할 수 있지만, 실제 URL이나 응답 필터에 적용되는 코드는 없습니다.

### 6.2 Supplier

`get_supplier_data()`는 supplier endpoint를 페이지별로 조회합니다.

1. supplier 레코드를 설정된 Raw table/key로 적재합니다.
2. 후속 조회에 쓸 `{SupplierId: Supplier}` 사전을 만듭니다.
3. 수집된 각 supplier에 대해 attachment endpoint를 페이지별 조회합니다.
   - attachment metadata에서 omit 필드를 뺍니다.
   - 각 Raw row에 `SupplierId`, `Supplier`를 추가합니다.
   - `DatatypeCode == "FILE"`인 첨부 중 `links`에 `name == "FileContents"`인 URL이 있으면 파일 내용을 GET합니다.
   - 파일은 CDF Files용 metadata와 content queue에 넣습니다. 파일 metadata는 `create_file_md()`에서 filename의 `/`, `\\`를 `_`로 바꾸고, MIME type을 파일명에서 추정합니다. external ID는 설정된 raw key를 사용하고, `scm`, `supplier_attachments` tag를 붙입니다.
4. 각 supplier에 대해 site endpoint를 페이지별 조회합니다.
   - site metadata를 Raw에 적재하면서 `SupplierId`, `Supplier`를 추가합니다.

첨부 파일 크기가 100 MiB보다 크면 경고 로그를 남기지만, 현재 코드에는 이 크기에서 업로드를 막는 제한은 없습니다.

### 6.3 Purchase Order

`get_purchase_order_data()`는 다음 순서로 동작합니다.

1. purchase order header endpoint를 페이지별 조회하고 header 레코드를 Raw에 적재합니다.
2. `POHeaderId`를 모아 각 header의 상세 endpoint 조회에 사용합니다.
3. 각 PO마다 lines endpoint를 페이지별 조회합니다.
   - line의 원본 key 앞에 `POHeaderId:`를 붙여 Raw key를 만듭니다.
   - columns에도 parent PO 식별자를 추가합니다.
4. 각 line마다 DFF(Descriptive Flex Fields) endpoint를 페이지별 조회합니다.
   - DFF도 `POHeaderId:`와 해당 DFF 설정 key를 조합한 key로 적재합니다.
   - parent PO 식별자를 columns에 추가합니다.
5. 각 PO마다 attachments endpoint를 조회합니다.
   - attachment metadata를 Raw에 적재하고 parent PO 식별자를 추가합니다.
   - 파일 첨부는 CDF Files queue에 넣으며 `scm`, `purchase_order_attachments` tag를 씁니다.

### 6.4 Request for Quotation (RFQ)

`get_request_for_quotation_data()`는 negotiation endpoint를 페이지별 조회하지만, 모든 negotiation을 처리하지는 않습니다.

1. `NegotiationType == "RFQ"`인 항목만 RFQ로 처리합니다.
2. RFQ header metadata를 Raw에 적재하고 `AuctionHeaderId`들을 모읍니다.
3. 각 RFQ의 lines endpoint를 페이지별 조회합니다. Raw key는 RFQ ID와 line key를 조합하며, columns에 parent RFQ 식별자를 넣습니다.
4. 각 line별 DFF endpoint를 조회하고, RFQ ID를 포함한 복합 key로 적재합니다.
5. 각 RFQ의 attachments endpoint를 조회합니다. Metadata는 Raw, 파일 본문은 CDF Files에 저장하며 `scm`, `request_for_quotation_attachments` tag를 사용합니다.

### 6.5 전체 결과 확정

Supplier, PO, RFQ가 차례대로 실행됩니다. 각 함수가 실패를 반환하면 `run_succeed`가 false가 되지만, stop 요청이 아닌 한 나머지 그룹도 시도합니다.

- 모두 성공: `last_run_utc`와 `last_run_epoch`를 현재 UTC 시각으로 갱신하고 state store를 synchronize합니다. Extraction Pipeline에 success run을 기록합니다.
- 하나 이상 실패: state store 시각을 갱신하지 않고 failure run을 기록합니다.
- 중간에 stop 요청: 현재 그룹/실행을 종료하며, 최종 state 시각은 갱신하지 않습니다.
- 처리되지 않은 예외: 바깥 `except`에서 stack trace를 로그로 남기고 pipeline failure run을 만듭니다.

## 7. CDF에 저장되는 두 종류의 데이터

### CDF Raw

`RawUploadQueue`에 `Row`를 넣습니다. 일반 레코드는 설정된 `raw_table`의 database/table과 `raw_key`를 사용합니다. 하위 테이블(PO/RFQ lines, DFF 등)은 parent ID를 key 앞에 붙여 상위 객체별 key 충돌을 피합니다. 일부 레코드는 parent 식별자도 columns에 추가합니다.

원본 JSON 전체가 항상 그대로 저장되는 것은 아닙니다. 각 query의 `omit_fields`에 든 항목은 제외되고, 코드가 parent ID나 Supplier 이름/ID를 추가할 수 있습니다.

### CDF Files

바이너리 첨부 본문은 `BytesUploadQueue`를 통해 Cognite Files에 넣습니다. `create_file_md()`가 external ID, space, 정리된 파일명, MIME type, tags를 만듭니다. attachment metadata 자체는 별도의 Raw table에도 들어갑니다.

## 8. 중지, 재시도, 오류 처리

- stop event는 각 상위 목록/parent 반복에서 확인합니다. 설정되면 해당 데이터 함수는 `False`를 돌려주거나 전체 실행이 중단됩니다.
- REST GET은 설정된 재시도 정책을 사용합니다. 그러나 실제 retry는 해당 Session/adapter가 연결된 HTTPS 요청에만 적용됩니다.
- API query 오류는 함수별로 로그를 남기고 실패 상태를 반환합니다. 일부 실패 후에도 다른 parent나 데이터 그룹을 계속 처리합니다.
- CDF Raw/Files queue는 context manager로 열고 닫습니다. 따라서 업로드 queue lifecycle은 각 데이터 영역의 범위에 묶여 있습니다.

## 9. 이해할 때 특히 주의할 구현상 세부사항

아래는 문서상의 의도가 아니라 **현재 코드에서 관찰되는 동작**입니다.

1. **마지막 실행 시각이 증분 필터로 쓰이지 않습니다.** `last_run_utc`/`last_run_epoch`는 읽혀 각 함수에 전달되지만 함수 내부 조회조건에 적용되지 않습니다.
2. **파일 다운로드 오류가 전체 함수 실패로 항상 반영되지는 않습니다.** 파일 GET/queue 처리 예외는 `except Exception`에서 로그만 남기며, `function_succeed=False`로 설정하지 않습니다. 일부 파일 문제에도 전체 실행이 성공으로 판정될 가능성이 있습니다.
3. **파일 content GET의 HTTP status를 검사하지 않습니다.** `session.get()` 뒤 `raise_for_status()`가 없어 HTTP 오류 응답 body가 content로 queue에 들어갈 여지가 있습니다.
4. **`get_*` 함수 인자 중 `last_run_utc`, `last_run_epoch`는 현재 사용되지 않습니다.** `file_upload_queue` 인자는 파일 처리에 쓰이고, stop event와 metrics도 전달됩니다.
5. **RFQ negotiation 요청에는 HTTP timeout이 전달되지 않습니다.** 현재 코드는 `timeout`을 URL `.format()`에 넣지만 `session.get(..., timeout=...)` 인자로 전달하지 않습니다. 따라서 그 요청은 requests의 기본 timeout 동작을 따릅니다.
6. **`run_extractor()`의 `states` 인자는 사용되지 않습니다.** 함수 안에서 설정된 Raw destination으로 별도의 `RawStateStore`를 만듭니다.
7. **query 설정 누락/키 오타는 일부 예외 처리에서 잡히지 않을 수 있습니다.** 예를 들어 필요한 JSON key나 설정 key가 없을 때 생기는 `KeyError`는 대부분의 API 예외 handler가 잡는 예외 목록에 포함되지 않아 최상위 handler까지 올라갈 수 있습니다.
8. **메트릭 이름과 증가 단위를 함께 읽어야 합니다.** 일부 메트릭은 레코드 수를 증가시키지만, attachment/line 등 일부 성공 메트릭은 항목 수가 아니라 처리된 parent 또는 batch 단위로 증가합니다.
9. **저장소의 YAML만으로 SCM endpoint 설정을 확인할 수 없습니다.** [config.yaml](./config.yaml)은 remote 설정 모드이며 endpoint 및 Raw 목적지가 포함돼 있지 않습니다. 실제 실행 시 로드되는 설정을 확인해야 합니다.

## 10. 코드 읽기 추천 순서

실제 파일을 보면서 다음 순서로 읽으면 흐름을 놓치기 어렵습니다.

1. [__main__.py](./scm_extractor/__main__.py): 직접 실행이 어디서 시작되는지
2. [config.py](./scm_extractor/config.py): 코드가 요구하는 설정 구조
3. [service.py](./scm_extractor/service.py): 직접 실행과 예약 실행 차이
4. [extractor.py](./scm_extractor/extractor.py)의 `create_state_store()`와 `run_extractor()`: 큰 실행 순서
5. 같은 파일의 `get_supplier_data()`: 기본 pagination, Raw row, 첨부 처리 패턴
6. `get_purchase_order_data()`: parent-child 관계, lines와 DFF 복합 key
7. `get_request_for_quotation_data()`: negotiation 필터와 RFQ 후속 조회
8. `create_file_md()` 및 [metrics.py](./scm_extractor/metrics.py): 파일 metadata와 관측 지표
9. [winservice.py](./scm_extractor/winservice.py): Windows에서 상시 실행하는 경우의 진입/중지 흐름

각 API 호출마다 “요청 URL은 어디서 설정되는가 → 응답에서 무엇을 읽는가 → Raw key는 무엇인가 → parent 값은 어디에 넣는가 → 어떤 오류가 실패로 집계되는가”를 확인하면, P6 API extractor에 재사용할 수 있는 패턴과 SCM에만 해당하는 로직을 구별할 수 있습니다.
