"""Test dashboard for PBL4 Driver AI. OpenCV-only; backward-compatible public API."""
from datetime import datetime
import textwrap
import cv2
import numpy as np
from AI.scripts.time_utils import duration_hms

class Dashboard:
    WINDOW = 'AI Driver Monitor'
    CANVAS_SIZE = (1600, 900)
    BG=(18,20,24); PANEL=(29,32,39); CARD=(37,41,49); BORDER=(66,72,83)
    WHITE=(242,244,247); MUTED=(164,174,186); GREEN=(120,210,110)
    AMBER=(70,188,255); RED=(95,100,245); CYAN=(230,200,90); PURPLE=(210,145,195)

    def __init__(self):
        self.action=None; self._buttons={}; self._eye_duration=0.; self._mouth_duration=0.; self._last_state={}
        cv2.namedWindow(self.WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.WINDOW,*self.CANVAS_SIZE)
        cv2.setMouseCallback(self.WINDOW,self._on_mouse)

    def _on_mouse(self,event,x,y,_flags,_param):
        if event != cv2.EVENT_LBUTTONUP: return
        try:
            _,_,w,h=cv2.getWindowImageRect(self.WINDOW)
            if w>0 and h>0:
                x=int(x*self.CANVAS_SIZE[0]/w); y=int(y*self.CANVAS_SIZE[1]/h)
        except cv2.error: pass
        for name,(x1,y1,x2,y2) in self._buttons.items():
            if x1<=x<=x2 and y1<=y<=y2: self.action=name

    def poll_action(self):
        key=cv2.waitKey(1)&0xFF
        if key in (ord('q'),27): return 'quit'
        action,self.action=self.action,None
        return action

    @staticmethod
    def _fit_frame(frame,width,height):
        if frame is None or not hasattr(frame,'shape') or frame.size==0:
            return np.full((height,width,3),(12,14,18),dtype=np.uint8)
        sh,sw=frame.shape[:2]
        scale=min(width/sw,height/sh)
        new=(max(1,int(sw*scale)),max(1,int(sh*scale)))
        resized=cv2.resize(frame,new,interpolation=cv2.INTER_AREA if scale<1 else cv2.INTER_LINEAR)
        top=(height-new[1])//2; left=(width-new[0])//2
        return cv2.copyMakeBorder(resized,top,height-new[1]-top,left,width-new[0]-left,cv2.BORDER_CONSTANT,value=(12,14,18))

    @classmethod
    def _text(cls,c,value,x,y,size=.45,color=None,thickness=1,max_width=None):
        s=str(value)
        if max_width is not None:
            while s and cv2.getTextSize(s,cv2.FONT_HERSHEY_SIMPLEX,size,thickness)[0][0]>max_width:
                s=s[:-1]
            if s!=str(value) and len(s)>3: s=s[:-3]+'...'
        cv2.putText(c,s,(int(x),int(y)),cv2.FONT_HERSHEY_SIMPLEX,size,color or cls.WHITE,thickness,cv2.LINE_AA)

    @classmethod
    def _box(cls,c,r,fill=None,border=None):
        x1,y1,x2,y2=map(int,r)
        cv2.rectangle(c,(x1,y1),(x2,y2),fill or cls.CARD,-1)
        cv2.rectangle(c,(x1,y1),(x2,y2),border or cls.BORDER,1,cv2.LINE_AA)

    @classmethod
    def _card(cls,c,r,title,accent=None):
        cls._box(c,r)
        x1,y1,x2,_=r
        cv2.rectangle(c,(x1+1,y1+1),(x1+5,y1+34),accent or cls.CYAN,-1)
        cls._text(c,title.upper(),x1+16,y1+23,.43,cls.MUTED,1,x2-x1-30)

    @classmethod
    def _progress(cls,c,r,value,color):
        x1,y1,x2,y2=map(int,r)
        cv2.rectangle(c,(x1,y1),(x2,y2),(65,70,78),-1)
        try: ratio=max(0.,min(1.,float(value)))
        except (ValueError,TypeError): ratio=0.
        if ratio>0: cv2.rectangle(c,(x1,y1),(x1+int((x2-x1)*ratio),y2),color,-1)

    @staticmethod
    def _num(value,digits=2):
        try: return f'{float(value):.{digits}f}' if value is not None else '--'
        except (TypeError,ValueError): return '--'

    @staticmethod
    def _safe_float(value,default=0.):
        try: return float(value)
        except (ValueError,TypeError): return default

    @staticmethod
    def _normalize_detections(state):
        detections=state.get('detections')
        if detections:
            out=[]
            for item in detections:
                try: out.append((str(item[0]),float(item[1])))
                except (TypeError,ValueError,IndexError,KeyError): continue
            return out
        names=state.get('detected_classes',[])
        if isinstance(names,str): names=[names]
        return [(str(n),None) for n in (names or [])]

    def _base(self,subtitle):
        c=np.full((900,1600,3),self.BG,dtype=np.uint8)
        self._text(c,'DRIVER SAFETY MONITOR',26,42,.86,self.WHITE,2)
        self._text(c,subtitle,28,68,.42,self.MUTED)
        now=datetime.now()
        self._text(c,now.strftime('%d/%m/%Y  %H:%M:%S'),1310,41,.48,self.CYAN,1,260)
        cv2.line(c,(25,87),(1575,87),self.BORDER,1)
        return c

    def _button(self,c,name,r,label,enabled=True):
        self._buttons[name]=r
        self._box(c,r,self.GREEN if enabled else self.BORDER,self.GREEN if enabled else self.BORDER)
        self._text(c,label,r[0]+19,r[1]+29,.48,self.BG if enabled else self.MUTED,2,r[2]-r[0]-25)

    def _simple_screen(self,frame,subtitle,heading,lines,buttons):
        c=self._base(subtitle)
        c[112:822,25:1000]=self._fit_frame(frame,975,710)
        self._box(c,(1018,112,1575,822),self.PANEL)
        self._text(c,heading,1040,155,.64,self.CYAN,2,510)
        for i,line in enumerate(lines[:16]):
            self._text(c,line,1040,205+i*32,.44,self.WHITE,1,515)
        self._buttons.clear()
        for name,r,label,enabled in buttons: self._button(c,name,r,label,enabled)
        cv2.imshow(self.WINDOW,c)

    def show_alignment(self,frame,status,metrics=None,can_continue=False):
        lines=[f'{k}: {v}' for k,v in (metrics or {}).items()]
        lines.extend(['','STATUS:']+textwrap.wrap(str(status),56))
        self._simple_screen(frame,'STEP 1 / FACE ALIGNMENT','ALIGNMENT CHECK',lines,[('continue',(1040,747,1265,798),'CONTINUE',can_continue),('quit',(1300,747,1545,798),'QUIT',True)])

    def show_calibration(self,frame,data):
        rows=['Remaining: '+self._num(data.get('remaining'),1)+' s',
              'Duration: '+self._num(data.get('duration'),1)+' s',
              'Face detected: '+str(bool(data.get('face_detected')))]
        rows += [f'{k.upper()}: {self._num(data.get(k),3)}' for k in ('ear','mar','yaw','pitch','roll')]
        rows += ['Valid samples: '+str(data.get('sample_count',0))]
        self._simple_screen(frame,'STEP 2 / PERSONAL CALIBRATION','BASELINE CALIBRATION',rows,[('quit',(1300,747,1545,798),'QUIT',True)])

    def show_message(self,frame,title,message,can_start=False):
        buttons=[]
        if can_start: buttons.append(('start',(1040,747,1280,798),'START MONITORING',True))
        buttons.append(('quit',(1300,747,1545,798),'QUIT',True))
        self._simple_screen(frame,'DRIVER SAFETY MONITOR',str(title),textwrap.wrap(str(message),60),buttons)

    def show_monitor(self,dashboard_frame,state=None):
        if state is None:
            cv2.imshow(self.WINDOW,dashboard_frame)
            return
        s=state or {}; c=self._base('YOLO + INDEPENDENT MEDIAPIPE / FUSION')
        self._last_state=s
        # Left: camera, then concise YOLO list.
        c[115:716,25:1000]=self._fit_frame(dashboard_frame,975,601)
        self._box(c,(25,115,196,150),(23,29,30),(23,29,30))
        self._text(c,'LIVE CAMERA',39,138,.42,self.GREEN,1)
        self._card(c,(25,730,1000,846),'YOLO DETECTIONS & CONFIDENCE',self.CYAN)
        det=self._normalize_detections(s)
        if not det: self._text(c,'No detections in current frame',44,792,.48,self.MUTED)
        for i,(name,conf) in enumerate(sorted(det,key=lambda t:t[1] if t[1] is not None else -1,reverse=True)[:5]):
            col=i%3; row=i//3; x=44+col*317; y=777+row*36
            suffix=f'  {conf*100:.1f}%' if conf is not None else '  (no confidence)'
            self._text(c,name.replace('_',' ')[:19]+suffix,x,y,.42,self.WHITE,1,295)
        # Right side: distinct non-overlapping cards.
        x1,x2=1018,1575
        self._card(c,(x1,115,x2,204),'CURRENT STATUS',self.GREEN)
        alert=str(s.get('confirmed_alert','NORMAL')).upper()
        detailed=str(s.get('detailed_alert') or alert).upper()
        eye_state=str(s.get('drowsy_state','IDLE')).upper()
        mouth_state=str(s.get('yawning_state','IDLE')).upper()
        mp_active=bool(s.get('mp_active',False))
        alert_color=self.RED if 'DROWSY' in alert else self.AMBER if alert in ('SUSPECTED','CONFLICT','TRACKING_LOST','UNKNOWN','YAWNING') else self.GREEN
        self._text(c,f'{alert}  /  {detailed}',1037,168,.54,alert_color,2,505)
        self._text(c,f'EYE: {eye_state}     MOUTH: {mouth_state}',1037,191,.36,self.MUTED,1,505)
        self._card(c,(x1,214,1290,328),'EYE CLOSED',self.RED)
        self._card(c,(1301,214,x2,328),'YAWNING / MOUTH',self.AMBER)
        eye=self._safe_float(s.get('eye_duration')); mouth=self._safe_float(s.get('mouth_duration'))
        et=self._safe_float(s.get('eye_threshold_seconds',2.),2.); mt=self._safe_float(s.get('yawn_threshold_seconds',1.),1.)
        self._text(c,f'{eye:.2f}s / {et:.1f}s',1037,267,.57,self.WHITE,2)
        self._progress(c,(1037,279,1270,289),eye/et if et>0 else 0,self.RED)
        self._text(c,eye_state,1037,313,.40,self.MUTED,1,230)
        self._text(c,f'{mouth:.2f}s / {mt:.1f}s',1320,267,.57,self.WHITE,2)
        self._progress(c,(1320,279,1555,289),mouth/mt if mt>0 else 0,self.AMBER)
        self._text(c,mouth_state,1320,313,.40,self.MUTED,1,225)
        self._card(c,(x1,339,x2,476),'MEDIAPIPE / BASELINE VERIFICATION',self.CYAN)
        baseline='VALID' if s.get('baseline_valid') else 'FALLBACK / NOT VALID'
        self._text(c,f'MediaPipe: {"ACTIVE" if mp_active else "IDLE"}   |   Baseline: {baseline}',1037,388,.43,self.GREEN if mp_active else self.MUTED,1,515)
        self._text(c,f'EAR: {self._num(s.get("mp_ear"),3)}   Threshold: {self._num(s.get("ear_threshold"),3)}',1037,416,.43,self.WHITE)
        self._text(c,f'MAR: {self._num(s.get("mp_mar"),3)}   Threshold: {self._num(s.get("mar_threshold"),3)}',1037,443,.43,self.WHITE)
        self._text(c,f'MP activations: {s.get("mp_total_activations",0)}',1037,466,.36,self.MUTED)
        self._card(c,(x1,487,x2,605),'FUSION / MEASUREMENT SOURCE',self.PURPLE)
        for i,(label,prefix) in enumerate((('EYE','drowsy'),('YAWN','yawning'))):
            x=1037+i*270
            valid=s.get(f'{prefix}_valid_samples',0)
            ratio=self._safe_float(s.get(f'{prefix}_vote_ratio'))
            self._text(c,f'{label}: {s.get("eye_source" if i==0 else "mouth_source", "NONE")}',x,536,.43,self.WHITE,1,245)
            self._progress(c,(x,547,x+239,557),ratio,self.PURPLE)
            self._text(c,f'MP sample: {"VALID" if valid else "UNAVAILABLE"}',x,584,.37,self.MUTED,1,246)
        self._card(c,(x1,616,x2,732),'PERFORMANCE / FRAME BUFFER',self.CYAN)
        run=self._safe_float(s.get('monitoring_duration_seconds'))
        try: run_text=duration_hms(run)
        except Exception: run_text=f'{run:.0f}s'
        self._text(c,f'Runtime: {run_text}     AI FPS: {self._num(s.get("ai_fps"),1)}',1037,663,.43,self.WHITE,1,510)
        self._text(c,f'Latency: total {self._num(s.get("pipeline_latency_ms"),1)}ms | YOLO {self._num(s.get("yolo_latency_ms"),1)}ms | MP {self._num(s.get("mp_latency_ms"),1)}ms',1037,690,.39,self.WHITE,1,515)
        self._text(c,f'Buffer: {s.get("buffer_count",0)}/{s.get("buffer_capacity",10)} frames  |  Instant: {s.get("instantaneous_status","--")}',1037,717,.39,self.MUTED,1,515)
        self._card(c,(x1,743,x2,846),'ESP / ALERT TRANSPORT',self.GREEN)
        mode=str(s.get('esp_mode','MOCK')).upper()
        connected=bool(s.get('esp_connected',False))
        self._text(c,f'Mode: {mode}   |   Connection: {"CONNECTED" if connected else "NOT CONFIRMED"}',1037,791,.43,self.WHITE,1,505)
        result=s.get('esp_result')
        packet=getattr(result,'packet',None) if result is not None else None
        self._text(c,f'Last packet: {str(packet).strip() if packet else "No state change"}',1037,820,.42,self.MUTED,1,505)
        self._buttons.clear()
        self._button(c,'quit',(1408,852,1575,891),'QUIT')
        cv2.imshow(self.WINDOW,c)

    def close(self):
        try: cv2.destroyWindow(self.WINDOW)
        except cv2.error: pass
