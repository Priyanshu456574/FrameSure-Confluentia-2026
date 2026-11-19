"""Generate the submission brief from current evidence (ReportLab only)."""
import json
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image, Flowable

ROOT=Path(__file__).resolve().parent.parent

class Architecture(Flowable):
    def __init__(self):
        super().__init__()
        self.width=475
        self.height=160
    def draw(self):
        c=self.canv
        boxes=[(0,104,210,'Browser: hash / chunks / retries'),(265,104,210,'HTTP API + durable SQLite / files'),
               (265,45,210,'Full-file SHA gate, then FFmpeg'),(0,45,210,'JSON: bytes / source / target')]
        c.setFont('Helvetica',9)
        for x,y,w,label in boxes:
            c.setFillColor(colors.HexColor('#edf7f5'));c.setStrokeColor(colors.HexColor('#159883'))
            c.roundRect(x,y,w,37,5,stroke=1,fill=1)
            c.setFillColor(colors.HexColor('#183746'));c.drawCentredString(x+w/2,y+15,label)
        for x1,y1,x2,y2 in [(210,122,265,122),(370,104,370,82),(265,63,210,63)]:
            c.line(x1,y1,x2,y2)
        c.setFont('Helvetica-Oblique',8)
        c.drawString(0,12,'Operational failure: retain verified data; three failed attempts produce FAILED, validity unknown.')

def main():
    summary=json.loads((ROOT/'evidence/validation_summary.json').read_text())
    bench=json.loads((ROOT/'evidence/full_video_benchmark.json').read_text())
    network=json.loads((ROOT/'evidence/network/controlled_results.json').read_text())
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='Title2',fontName='Helvetica-Bold',fontSize=27,leading=32,textColor=colors.HexColor('#163342'),spaceAfter=15))
    styles['BodyText'].fontSize=10; styles['BodyText'].leading=15; styles['BodyText'].spaceAfter=9
    styles['Heading2'].textColor=colors.HexColor('#087f70')
    def p(text,style='BodyText'): return Paragraph(text,styles[style])
    def table(rows,widths):
        t=Table([[p(escape(str(cell))) for cell in row] for row in rows],colWidths=widths,repeatRows=1)
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e6f2ef')),('VALIGN',(0,0),(-1,-1),'TOP'),
            ('GRID',(0,0),(-1,-1),.4,colors.HexColor('#cbdad9')),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
        return t
    story=[p('FRAMESURE 2.0.0','Title2'),p('Verified transfer. Explainable frame integrity.','Heading2'),
        p('IIT (BHU) Confluentia 2026 technical challenge | Reviewed 19 November 2026'),
        p('Problem and proposed solution','Heading2'),
        p('Deliver a 30-second video through changing connectivity, verify intact delivery, then validate decoding and examine frames at a target 30 FPS. FrameSure preserves the existing Python/browser implementation and uses durable indexed chunks, SHA-256 gates, resumable sessions and evidence-qualified frame reports.'),
        p('Current release evidence','Heading2'),
        table([['Check','Observed result'],['Python suite',f"{summary['passed']} passed; {summary['skipped']} skipped; {summary['failures']} failures; {summary['errors']} errors"],
            ['Frontend regressions','Seven frontend behavior checks and JavaScript parsing passed'],
            ['Video examples','Fresh valid/gap/invalid reports plus actual FFmpeg timeout recovery'],
            ['HTTP benchmark',f"{bench['trial_count']} real HTTP/TCP trials with actual video decoding"],
            ['Browser workflow','User-completed session and report observed; automated suite BLOCKED'],
            ['Hosted CI / GitHub','Configured / prepared; not run or published']], [150,325]),
        p('Release decision','Heading2'),p('Ready for local technical review. Remaining full browser scenarios, physical-network evidence, authenticated publication and late acceptance prevent unconditional submission certification.'),
        PageBreak(),p('Architecture and methodology','Title2'),Architecture(),
        p('Reliable transfer','Heading2'),p('The client hashes the file and sends fixed 256 KiB chunks. The backend validates chunk length and digest, writes and fsyncs an atomic file, commits SQLite metadata and then acknowledges. Retries are idempotent; conflicting replays fail. Finalization orders all pieces and rechecks whole-file bytes before decoding.'),
        p('Frame evidence','Heading2'),p('FFprobe records source frame timestamps and indices. FFmpeg verifies decoding, fingerprints source frames with passthrough timing, and separately samples fps=30. Timestamp gaps and repeated luma hashes are heuristic warnings. A static scene can resemble repeated frames; a resampled 900-frame output does not establish a complete camera recording.'),
        p('Improvements in this release','Heading2'),p('Timeouts, missing tools, OS execution errors and signal termination leave a retryable verified upload. A persisted three-attempt budget ends in FAILED with video validity unknown, rather than false corruption rejection. Concurrent finalization reuses completed reports. Client report reset, stale-hash guards and locked file selection protect state; ACK rate and outage labels state their actual meaning.'),
        p('Storage and platform','Heading2'),p('SQLite upload/chunk rows, per-session POSIX locks, numbered chunk files and an atomically assembled final file. Python standard library plus FFmpeg/FFprobe; macOS/Linux local filesystem. CLI host, port and data directory configuration.'),
        PageBreak(),p('Executed validation','Title2'),
        p(f"Python {summary['python']}; {escape(summary['platform'])}. Exact logs and tool versions are included in evidence/. The reviewed baseline and timeout checkpoint were independently rechecked. Current logs and reports are separated from historical baseline evidence."),
        p('Controlled HTTP benchmark','Heading2')]
    rows=[['Condition / strategy','Payload bytes','Retries','Seconds']]
    for r in bench['trials']:
        rows.append([('Stable' if r['scenario']=='stable' else 'TCP interrupted')+' / '+r['architecture'],r['wire_payload_bytes'],r['retry_count'],r['elapsed_wall_seconds']])
    story.extend([table(rows,[220,100,65,90]),Spacer(1,12),
        p('One 1,386,829-byte fixture and one deterministic socket interruption per disrupted trial. The interrupted resumable strategy sent 524,288 fewer payload bytes. Timing includes decode work on localhost; no statistical reliability, real radio strength or universal speed advantage is claimed.'),
        p('Fresh sample reports','Heading2'),table([['Input','Observed output'],['Valid 30-second fixture','COMPLETED; 900 source frames, 900 target samples; zero heuristic anomalies'],['Synthetic timestamp gap','COMPLETED; 870 source frames, 900 target samples; one gap warning'],['Non-video bytes','REJECTED; matching transfer SHA but invalid video container']], [170,305]),
        p('Tests cover','Heading2'),p('Checksum and size failures, missing/corrupt pieces, replay/conflict, out-of-order and concurrent writes, restart/retry, HTTP framing, invalid duration/metadata, valid/invalid/truncated video, gap/static-scene evidence, bounded timeout recovery and real concurrent duplicate HTTP finalization. DOM-double tests exercise client state ownership. Real browser upload compatibility remains unproven.'),
        PageBreak(),p('Observed application','Title2'),p('Genuine observed user-completed session: matching SHA, 100% acknowledged and 900/900 frames. This is user execution and completed-session recovery, not an agent-executed fresh upload or interrupted-refresh proof. Later minor client changes were regression-tested with API doubles.')])
    screenshot=ROOT/'evidence/browser/selection_verified.jpg'
    if screenshot.exists():
        image=Image(str(screenshot)); scale=min(475/image.imageWidth,565/image.imageHeight)
        image.drawWidth=image.imageWidth*scale;image.drawHeight=image.imageHeight*scale;story.append(image)
    story.extend([PageBreak(),p('Network and requirement coverage','Title2'),
        p('Executed controlled impairment','Heading2'),
        p('An isolated localhost HTTP proxy adds delay, body pacing, a TCP shutdown or an HTTP failure. No host network settings were changed. All profiles verified the complete SHA and decoded 900 frames.')])
    network_rows=[['Profile','Controlled setting','Retries','Seconds']]
    for row in network['results']:
        settings={'stable':'None','latency':'75 ms per PUT','throttled':'524,288 B/s body pacing',
                  'TCP interruption':'One close after 8,192 bytes','transient request failure':'One HTTP 503'}
        network_rows.append([row['scenario'],settings[row['scenario']],row['retries'],row['elapsed_seconds']])
    story.extend([table(network_rows,[130,205,60,80]),Spacer(1,10),
        p('This is userspace proxy impairment, not kernel packet loss or physical radio measurement. Wi-Fi/mobile handoff and physical signal degradation are NOT TESTED.'),
        p('Evidence-based requirement coverage','Heading2'),
        table([['Classification','Grouped obligations','Reason'],
            ['Fully implemented','10','Byte/processing gates, reports, local tests, documentation and package'],
            ['Partially implemented','6','Physical network constraints, broad anomaly evidence and full browser matrix'],
            ['Not implemented','0','No grouped requirement silently omitted']], [130,120,225]),
        p('Sixteen internally grouped official obligations; not an official competition scoring system. The detailed traceability matrix maps implementation locations to actual tests and remaining gaps.'),
        p('Browser and security limits','Heading2'),
        p('Portable Playwright suite: actual launch exit 2, BLOCKED. Normal user completion is observed, while full automated outage, refresh, invalid and timeout cases remain pending. The prototype has no auth, TLS, global disk budget or decoder sandbox; use localhost only.')])
    story.extend([PageBreak(),p('Demonstrate and submit','Title2'),p('Run and verify','Heading2'),
        p('Install Python 3.10+ and FFmpeg/FFprobe. From the source folder run scripts.preflight, scripts.validate, then backend.api. Open http://127.0.0.1:8877. See README.md and docs/DEMO_GUIDE.md for exact commands and the 4-minute demonstration script.'),
        p('Limitations and future scope','Heading2'),p('Real interface switching and physical packet loss, mobile/Safari support, broad VFR/codec evaluation and capture-side ground truth remain pending. This is a loopback prototype without authentication, TLS, global disk quotas or decoder sandboxing. Future priorities are browser recovery evidence, measured network trials and worker isolation.'),
        p('Submission and publication','Heading2'),p('The official deadline was 15 November 2026 at 11:59 PM IST. Confirm late acceptance with the organizers. The supplied PS has no numeric scoring rubric. No email was sent. GitHub authentication is unavailable; a cleaned local commit and Git bundle are supplied with exact publication commands. Public visibility permission and contributor identity were not assumed. The original PS PDF is excluded from the release.'),
        p('Conclusion','Heading2'),p('The revised prototype passes its complete local Python suite and real HTTP/video benchmarks. The evidence supports local byte reliability and qualified frame analysis. Remaining browser scenarios, physical-network claims and external submission remain explicitly separate pending work.')])
    def footer(c,d):
        c.setFont('Helvetica',8);c.setFillColor(colors.HexColor('#557079'))
        c.drawString(56,29,'FrameSure 2.0.0 | Audited local prototype | 19 November 2026');c.drawRightString(539,29,str(d.page))
    out=ROOT/'FrameSure_Submission_Brief.pdf'
    SimpleDocTemplate(str(out),pagesize=A4,leftMargin=56,rightMargin=56,topMargin=46,bottomMargin=48,
        title='FrameSure - Reviewed Technical Submission').build(story,onFirstPage=footer,onLaterPages=footer)
    print(out)

if __name__=='__main__': main()
