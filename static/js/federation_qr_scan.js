document.addEventListener('DOMContentLoaded',()=>{
  const input=document.getElementById('qr-image');
  const output=document.getElementById('qr-payload');
  const status=document.getElementById('qr-status');
  if(!input||!output||!status)return;
  if(!('BarcodeDetector'in window)){
    status.textContent='QR-Erkennung ggf. nicht verfügbar; Payload kann manuell eingefügt werden.';
    return;
  }
  input.addEventListener('change',async()=>{
    const file=input.files&&input.files[0];
    if(!file)return;
    try{
      const image=await createImageBitmap(file);
      const detector=new BarcodeDetector({formats:['qr_code']});
      const codes=await detector.detect(image);
      if(!codes.length){status.textContent='Kein QR-Code erkannt.';return;}
      output.value=codes[0].rawValue||'';
      status.textContent='QR-Code erkannt.';
    }catch(_error){status.textContent='QR-Code konnte nicht gelesen werden.';}
  });
});
