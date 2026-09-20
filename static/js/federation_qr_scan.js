document.addEventListener('DOMContentLoaded',()=>{
  const imageInput=document.getElementById('qr-image');
  const cameraInput=document.getElementById('qr-camera-image');
  const cameraButton=document.getElementById('qr-camera-button');
  const output=document.getElementById('qr-payload');
  const status=document.getElementById('qr-status');
  if(!imageInput||!cameraInput||!cameraButton||!output||!status)return;

  const supported='BarcodeDetector' in window;
  if(!supported){
    status.textContent='Die QR-Erkennung wird von diesem Browser nicht direkt unterstützt. Ein QR-Payload kann weiterhin manuell eingefügt werden.';
  }

  cameraButton.addEventListener('click',()=>cameraInput.click());

  async function decode(input){
    const file=input.files&&input.files[0];
    if(!file)return;
    if(!supported){
      status.textContent='Dieses Gerät kann das Kamerabild aufnehmen, aber der Browser bietet keine QR-Erkennung. Bitte einen unterstützten Browser verwenden oder den Payload manuell einfügen.';
      return;
    }
    status.textContent='QR-Code wird gelesen …';
    let image;
    try{
      image=await createImageBitmap(file);
      const detector=new BarcodeDetector({formats:['qr_code']});
      const codes=await detector.detect(image);
      if(!codes.length){
        status.textContent='Kein QR-Code erkannt. Bitte näher herangehen und erneut versuchen.';
        return;
      }
      const value=(codes[0].rawValue||'').trim();
      if(!value.startsWith('sofp://peer/')){
        status.textContent='Der QR-Code ist kein SimpleOffice-Federation-Connect-Code.';
        return;
      }
      output.value=value;
      status.textContent='SimpleOffice Connect QR-Code erkannt. Peer kann jetzt übernommen werden.';
    }catch(_error){
      status.textContent='QR-Code konnte nicht gelesen werden.';
    }finally{
      if(image&&typeof image.close==='function')image.close();
    }
  }

  imageInput.addEventListener('change',()=>decode(imageInput));
  cameraInput.addEventListener('change',()=>decode(cameraInput));
});
