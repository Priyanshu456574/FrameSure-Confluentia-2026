"""Generate an evidence-labelled, judge-readable PDF overview with ReportLab."""
from __future__ import annotations

import json
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, PageBreak

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'FrameSure_Submission_Brief.pdf'
DATA=json.loads((ROOT/'evidence'/'full_video_benchmark.json').read_text())
SUMMARY=json.loads((ROOT/'evidence'/'validation_summary.json').read_text())
NAVY=colors.HexColor('#102433'); TEAL=colors.HexColor('#00a686'); LIGHT=colors.HexColor('#e8f7f2')
GREY=colors.HexColor('#5c707b'); RULE=colors.HexColor('#dae4e7')
import reportlab
FONT_DIR=Path(reportlab.__file__).resolve().parent/'fonts'
pdfmetrics.registerFont(TTFont('DejaVu',str(FONT_DIR/'Vera.ttf')))
pdfmetrics.registerFont(TTFont('DejaVuBold',str(FONT_DIR/'VeraBd.ttf')))
style={
 'title':ParagraphStyle('title',fontName='DejaVuBold',fontSize=22,leading=29,textColor=NAVY,spaceAfter=12),
 'subtitle':ParagraphStyle('sub',fontName='DejaVu',fontSize=10,leading=16,textColor=GREY,spaceAfter=12),
 'h1':ParagraphStyle('h1',fontName='DejaVuBold',fontSize=12,leading=19,textColor=NAVY,spaceBefore=16,spaceAfter=7),
 'body':ParagraphStyle('body',fontName='DejaVu',fontSize=8.7,leading=14,spaceAfter=7),
 'small':ParagraphStyle('small',fontName='DejaVu',fontSize=7.4,leading=12,spaceAfter=4),
 'table':ParagraphStyle('table',fontName='DejaVu',fontSize=7,leading=11),
 'tablehead':ParagraphStyle('tablehead',fontName='DejaVuBold',fontSize=7,leading=11,textColor=colors.white),
 'light':ParagraphStyle('light',fontName='DejaVu',fontSize=8.2,leading=13,textColor=GREY),
}

def P(t,k='body'):return Paragraph(t,style[k])

def tbl(rows,widths=None,header=True):
 values=[]
 for i,row in enumerate(rows):
  values.append([P(str(cell),'tablehead' if header and i==0 else 'table') for cell in row])
 table=Table(values,colWidths=widths,hAlign='LEFT',repeatRows=1 if header else 0)
 table.setStyle(TableStyle([
  ('BACKGROUND',(0,0),(-1,0),NAVY if header else LIGHT),
  ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f2f6f6')]),
  ('BOX',(0,0),(-1,-1),.45,RULE),('LINEBELOW',(0,0),(-1,0),.6,TEAL),
  ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),9),
  ('RIGHTPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),6),
  ('BOTTOMPADDING',(0,0),(-1,-1),6),
 ]))
 return table

story=[]
story.extend([P('FRAMESURE','title'),P('VERIFIED TRANSFER, EXPLAINABLE FRAME INTEGRITY','subtitle'),
 P('IIT (BHU) Confluentia 2026  |  Nihilent / nSEPIA Technical Challenge','body'),
 P('<b>Challenge:</b> Send a 30-second video through varying connectivity (20%-100% stated network strength), confirm complete uncorrupted delivery, and diagnose 30-FPS frame integrity without confusing network losses with capture/encoding losses.'),
 tbl([[str(SUMMARY['passed']),str(DATA['trial_count']),'2','30 FPS'],['automated tests passed','actual local HTTP benchmark trials','integrity layers: bytes + decoded video','analysis target']], [120,120,120,120],header=False),
 P('Executive decision','h1'),
 P('The existing zero-third-party-Python-runtime localhost system uses resumable 256-KiB chunks, SQLite-persisted upload state, file fsync/atomic renames, SHA-256 verification, and FFprobe/FFmpeg-based analysis. Add bounded retry, adaptive browser concurrency, a clearly labeled network-fault emulator, and source-vs-target frame evidence.'),
 P('Requirement traceability at a glance','h1'),
 tbl([['Official obligation','Implemented mechanism','Validation status'],
 ['End-to-end 30-second video under variable connectivity','256-KiB chunks, resume, retry and emulator','Local proof; physical radio testing pending'],
 ['Never silently accept partial/damaged transfer','Chunk size/hash + full size/SHA verification before decode','Tested; corrupt/partial rejected'],
 ['Validate and decode video','ffprobe + FFmpeg full decode, error reporting','Tested with valid/invalid clips'],
 ['30-FPS frames; identify anomalies and positions','Source PTS + FFmpeg fps=30 target; timestamp and visual warnings','Synthetic gap localized at ~11s'],
 ['Implementation, docs, tests, sample report','Code ZIP, README, logs, JSON, demo guide','Included in package']], [141,194,145]),
 P('See <b>docs/REQUIREMENT_TRACEABILITY.md</b> for the full PDF-to-code-to-test matrix. No official numeric judging rubric exists in the supplied challenge.','small'),
 P('Why a single HTTP request fails','h1'),
 P('TCP retransmits packet loss while its connection remains viable, but a broken HTTP body usually loses application-level progress. A 95%-complete conventional upload often must restart. Retries also risk duplicate/conflicting data unless they are idempotent. A final received byte is not equivalent to fsynced storage, verified file content, or correct source video.')
])
story.append(PageBreak())
story.extend([
 P('Engineering architecture and recovery','title'),
 tbl([['Step','Safety property','What the reviewer can verify'],
 ['Session allocation','UUID + declared file length/hash, bounded size','GET session status'],
 ['Chunk acceptance','SHA-256, exact offset-length, atomic fsync+rename','Corrupt chunk gets HTTP 422'],
 ['Duplicate/out-of-order','Same content idempotent; conflict HTTP 409','Repeat request; reorder chunks'],
 ['Reconnect and refresh','SQLite source of truth; same-file SHA check','Restart server or reselect file'],
 ['Finalization','Rehash stored parts; exact file SHA before decoder','No false completion with missing data'],
 ['Video processing','Container, full decode, source timestamps','Valid 30s MP4 produces source frames'],
 ['Evidence report','30-FPS target sampled separately and labeled','JSON with timestamps, source/target arrays']], [123,183,174]),
 P('Runtime model','h1'),
 P('<b>Browser  >  HTTP API  >  SQLite + POSIX chunks  >  whole-file SHA-256 gate  >  FFprobe / FFmpeg  >  JSON integrity report.</b> Per-session POSIX locks serialize finalization. A processing timeout retains verified data in VERIFYING for retry. After three unsuccessful attempts, FAILED records unknown video validity. Invalid video is REJECTED; successful processing is COMPLETED.'),
 P('Scientifically defensible frame detection','h1'),
 P('<b>Cryptographic byte integrity:</b> SHA-256 confirms equality with the sender-provided byte sequence; it does not attest what the camera actually captured. <b>Video validity:</b> FFprobe and FFmpeg verify container and decodability. <b>Source evidence:</b> best-effort source timestamps, gaps/non-monotonicity and suspiciously repeated low-resolution luma hashes are labeled heuristic, not certain. <b>Output sampling:</b> `fps=30` creates a separate timeline and may duplicate or omit frames.'),
 tbl([['Source video','Source frames','30-FPS samples','Interpretation'],
 ['Valid testsrc2, 30 sec','900','900','No heuristic anomalies found'],
 ['Synthetic 1.0s timestamp gap','870','900','Gap near 11s; heuristic, not proof of capture loss'],
 ['Legitimate static scene','900','900','Repeated visual-content warning, not verified loss'],
 ['Not a video container','-','-','SHA can match, but video is REJECTED']], [157,95,100,122]),
])
story.append(PageBreak())
story.extend([
 P('Executed evidence and engineering audit','title'),
 P('Local tests used Python 3.11.9 and FFmpeg/FFprobe 7.0. The recorded results are measured. A fresh ZIP extraction also passed the complete Python suite; the original release is preserved.','body'),
 tbl([['Validation','Observed result'],
 ['Automated unittest suite',f"{SUMMARY['passed']} passed, {SUMMARY['skipped']} skipped, {SUMMARY['failures']} failures, {SUMMARY['errors']} errors"],
 ['Frontend checks','Seven behavior checks and JavaScript parsing passed'],
 ['Stable HTTP transfer','Both architectures verified the 1,386,829-byte 30-second video'],
 ['Injected TCP disconnect','Both recovered and delivered verified bytes'],
 ['Browser evidence','User completion/report observed; full automated suite BLOCKED']], [179,301]),
 P('One controlled interrupted HTTP upload','h1'),
 tbl([['Metric','Single request','FrameSure resumable'],
 ['Payload bytes sent','2,091,341','1,567,053'],
 ['Payload overhead','50.8%','13.0%'],
 ['Retries','1','1'],
 ['File hash matched','Yes','Yes'],
 ['Source video frames','900','900']], [165,157,158]),
 P('<b>Interpretation:</b> in this exact one-interruption test, FrameSure avoided 524,288 bytes of unnecessary retransmission. Stable transfers were equal at 1,386,829 payload bytes. This one-seed loopback benchmark is not statistical evidence of reliability under physical packet loss or real 20%-to-100% radio strength.'),
 P('Independent reliability review','h1'),
 P('Covered: malformed hashes, conflicting replay, partial/out-of-order chunks, concurrent requests, duplicate finalization, stored-byte corruption, restart, invalid/truncated video and source-gap evidence. Five isolated proxy profiles and an actual FFmpeg timeout/retry passed. Retries are persisted and bounded. Acknowledgment follows filesystem sync and database commit.'),
 P('Limitations to disclose','h1'),
 P('Physical Wi-Fi/mobile switching and IP packet loss, mobile/Safari support, broad codec/VFR coverage and load testing remain unverified. Proxy shaping is not physical network testing. Full browser outage/refresh/invalid/timeout tests are blocked. The localhost prototype lacks login, TLS and a decoder sandbox; capture-time loss needs independent evidence.'),
])
story.append(PageBreak())
story.extend([
 P('Run, demonstrate and submit','title'),
 P('1. Run the prototype','h1'),
 P('Install Python 3.10+ and FFmpeg/FFprobe, open a terminal in the extracted ZIP root, and run:'),
 P('`python3 -m backend.api --host 127.0.0.1 --port 8877 --data-dir runtime-data`'),
 P('Then visit <b>http://127.0.0.1:8877</b> and select <b>samples/valid_30s_30fps.mp4</b>.'),
 P('2. Reproduce the verification','h1'),
 P('`python3 -m unittest discover -s tests -v`'),
 P('`python3 -m scripts.benchmark --out evidence/full_video_benchmark.json`'),
 P('Review source reports in <b>evidence/</b>, full API and failure semantics in <b>docs/ARCHITECTURE.md</b>, and the 4-minute narration in <b>docs/BROWSER_DEMO_SCRIPT.md</b>.'),
 P('3. Demo sequence','h1'),
 P('Show valid 30-second upload; change synthetic strength; press "Emulate 5s outage"; let the client recover; show matching SHA; inspect source and target frames; upload a truncated invalid MP4 and show explicit rejection; finish with measured baseline comparison and clear limits.'),
 P('4. Submission package','h1'),
 P('Includes backend code, frontend HTML/JS/CSS, tests, five reproducible sample videos, the benchmark runner, integrity JSON reports, architecture and requirement matrix, security review, demo script, unofficial judge review and a prepared email draft.'),
 P('Public source repository','h1'),
 P('<link href="https://github.com/Priyanshu456574/FrameSure-Confluentia-2026">github.com/Priyanshu456574/FrameSure-Confluentia-2026</link>'),
 P('Official submission details (from challenge PDF page 3)','h1'),
 tbl([['Submission destination','iitbhu_2026@nihilent.com'],
 ['Deadline','15 November 2026, 11:59 PM IST'],
 ['Suggested subject','Confluentia 2026 | Technical Challenge | [Your Name / Team]'],
 ['Send','Repository/ZIP, concise documentation, test evidence, sample integrity report']], [169,311],header=False),
 P('Release declaration','h1'),
 P('This is a <b>working, locally tested prototype</b>. The stated deadline has elapsed; late acceptance is unknown and no email was sent. Browser and physical-network limits remain disclosed. No real-radio reliability rate or definitive camera frame-loss claim is made.'),
 P('Document generated from the packaged source, executed tests and measured benchmark; no invented benchmark outcomes.','small')
])

def footer(canvas,doc):
 canvas.setStrokeColor(RULE);canvas.line(19*mm,16*mm,191*mm,16*mm)
 canvas.setFont('DejaVu',7);canvas.setFillColor(GREY)
 canvas.drawString(19*mm,11*mm,'FrameSure | Confluentia 2026 | Evidence-constrained engineering submission')
 canvas.drawRightString(191*mm,11*mm,f'Page {doc.page}')

SimpleDocTemplate(str(OUT),pagesize=A4,leftMargin=19*mm,rightMargin=19*mm,
                  topMargin=18*mm,bottomMargin=22*mm,title='FrameSure | Submission Brief').build(story,onFirstPage=footer,onLaterPages=footer)
print(OUT,OUT.stat().st_size)
