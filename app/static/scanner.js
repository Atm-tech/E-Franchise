(() => {
  document.querySelectorAll('input[name="sku"],input[name="dispatch_number"]').forEach(input => {
    const button = document.createElement('button'); button.type='button'; button.textContent='Scan with camera';
    input.parentElement.after(button);
    button.addEventListener('click',async()=>{
      if (!navigator.mediaDevices) { alert('Camera scanning is unavailable. Use a handheld scanner or type the barcode.'); return; }
      let stream, controls;let running=true;
      const panel=document.createElement('div');const video=document.createElement('video');video.autoplay=true;video.playsInline=true;video.style.maxWidth='100%';
      const stop=document.createElement('button');stop.type='button';stop.textContent='Close camera';panel.append(video,stop);button.after(panel);button.disabled=true;
      const close=()=>{running=false;controls?.stop();stream?.getTracks().forEach(track=>track.stop());panel.remove();button.disabled=false;};stop.onclick=close;
      try {
        if (!('BarcodeDetector' in window)) {
          if (!window.ZXingBrowser) await new Promise((resolve,reject)=>{
            const script=document.createElement('script');script.src='/static/vendor/zxing-browser.min.js';script.onload=resolve;script.onerror=reject;document.head.append(script);
          });
          if (!running) return;
          const reader=new ZXingBrowser.BrowserMultiFormatReader();
          controls=await reader.decodeFromConstraints({video:{facingMode:'environment'},audio:false},video,(result,error,currentControls)=>{
            if(result && running){controls=currentControls;input.value=result.getText();input.dispatchEvent(new Event('change',{bubbles:true}));close();input.focus();}
          });
          if (!running) controls.stop();
          setTimeout(close,60000);return;
        }
        stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'},audio:false});video.srcObject=stream;await video.play();
        const detector=new BarcodeDetector();
        const scan=async()=>{
          if(!running)return;
          try {const codes=await detector.detect(video);if(codes.length){input.value=codes[0].rawValue;input.dispatchEvent(new Event('change',{bubbles:true}));close();input.focus();return;}}
          catch {close();return;}
          setTimeout(scan,250);
        };
        scan();setTimeout(close,60000);
      } catch {close();alert('Camera could not be opened. Use the scanner input instead.');}
    });
  });
})();
