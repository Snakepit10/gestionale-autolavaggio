import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async


async def _safe_group_discard(consumer):
    """Wrapper difensivo per disconnect: se connect() ha rifiutato
    l'handshake (utente anonimo, sessione scaduta) ritornando prima di
    settare self.room_group_name, channels chiama comunque disconnect()
    e accedere all'attributo direttamente solleverebbe AttributeError.
    Skip silenzioso in quel caso.
    """
    group = getattr(consumer, 'room_group_name', None)
    if group:
        await consumer.channel_layer.group_discard(group, consumer.channel_name)


class PostazioneConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        from django.contrib.auth.models import AnonymousUser

        if isinstance(self.scope["user"], AnonymousUser):
            await self.close()
            return
            
        self.postazione_id = self.scope['url_route']['kwargs']['postazione_id']
        self.room_group_name = f'postazione_{self.postazione_id}'
        
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        await _safe_group_discard(self)

    async def receive(self, text_data):
        data = json.loads(text_data)
        message_type = data.get('type')
        
        if message_type == 'aggiorna_stato_item':
            await self.aggiorna_stato_item(data)
    
    async def aggiorna_stato_item(self, data):
        item_id = data.get('item_id')
        nuovo_stato = data.get('stato')
        
        # Aggiorna il database
        await self.update_item_status(item_id, nuovo_stato)
        
        # Invia aggiornamento al gruppo
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'item_status_update',
                'item_id': item_id,
                'stato': nuovo_stato,
                'timestamp': data.get('timestamp')
            }
        )
    
    async def nuovo_ordine(self, event):
        await self.send(text_data=json.dumps({
            'type': 'nuovo_ordine',
            'ordine': event['ordine']
        }))
    
    async def item_status_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'item_status_update',
            'item_id': event['item_id'],
            'stato': event['stato'],
            'timestamp': event['timestamp']
        }))
    
    async def order_status_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'order_status_update',
            'ordine_id': event['ordine_id'],
            'numero_progressivo': event['numero_progressivo'],
            'vecchio_stato': event['vecchio_stato'],
            'nuovo_stato': event['nuovo_stato'],
            'timestamp': event['timestamp']
        }))
    
    @database_sync_to_async
    def update_item_status(self, item_id, stato):
        from apps.ordini.models import ItemOrdine
        from django.utils import timezone
        
        try:
            item = ItemOrdine.objects.get(id=item_id)
            item.stato = stato
            
            if stato == 'in_lavorazione' and not item.inizio_lavorazione:
                item.inizio_lavorazione = timezone.now()
            elif stato == 'completato' and not item.fine_lavorazione:
                item.fine_lavorazione = timezone.now()
            
            item.save()
            return True
        except ItemOrdine.DoesNotExist:
            return False


class OrdiniConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        from django.contrib.auth.models import AnonymousUser

        if isinstance(self.scope["user"], AnonymousUser):
            await self.close()
            return

        self.room_group_name = 'ordini_list'
        
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        await _safe_group_discard(self)

    async def nuovo_ordine_created(self, event):
        await self.send(text_data=json.dumps({
            'type': 'nuovo_ordine_created',
            'ordine_id': event['ordine_id'],
            'numero_progressivo': event['numero_progressivo']
        }))
    
    async def order_status_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'order_status_update',
            'ordine_id': event['ordine_id'],
            'numero_progressivo': event['numero_progressivo'],
            'vecchio_stato': event['vecchio_stato'],
            'nuovo_stato': event['nuovo_stato'],
            'stato_display': event['stato_display'],
            'timestamp': event['timestamp']
        }))

    async def nuovo_ordine(self, event):
        await self.send(text_data=json.dumps({
            'type': 'nuovo_ordine',
            'ordine_id': event.get('ordine_id'),
            'numero_progressivo': event.get('numero_progressivo'),
            'timestamp': event.get('timestamp'),
            'ordine': event.get('ordine')  # Per compatibilità con vecchi messaggi
        }))

    async def nuova_prenotazione(self, event):
        """Notifica realtime: nuova prenotazione cliente in_attesa.

        Inviata da apps/clients/views.py::crea_prenotazione_pub quando
        un cliente (anonimo o loggato) richiede una prenotazione.
        """
        await self.send(text_data=json.dumps({
            'type': 'nuova_prenotazione',
            'prenotazione_id': event.get('prenotazione_id'),
            'codice': event.get('codice'),
            'cliente': event.get('cliente'),
            'data': event.get('data'),
            'ora': event.get('ora'),
            'servizi': event.get('servizi'),
            'tipo_auto': event.get('tipo_auto'),
            'timestamp': event.get('timestamp'),
        }))

    async def ordine_modificato(self, event):
        await self.send(text_data=json.dumps({
            'type': 'ordine_modificato',
            'ordine_id': event.get('ordine_id'),
            'numero_progressivo': event.get('numero_progressivo'),
            'timestamp': event.get('timestamp')
        }))

    async def pagamento_aggiunto(self, event):
        await self.send(text_data=json.dumps({
            'type': 'pagamento_aggiunto',
            'ordine_id': event.get('ordine_id'),
            'numero_progressivo': event.get('numero_progressivo'),
            'timestamp': event.get('timestamp')
        }))


class DashboardConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        from django.contrib.auth.models import AnonymousUser

        if isinstance(self.scope["user"], AnonymousUser):
            await self.close()
            return
            
        self.room_group_name = 'dashboard_stats'
        
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        await _safe_group_discard(self)

    async def stats_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'stats_update',
            'stats': event['stats']
        }))


class MessaggiConsumer(AsyncWebsocketConsumer):
    """Consumer per la pagina /messaggi/ (inbox WhatsApp).

    Subscribe al group 'messaggi_wa' e inoltra al frontend gli eventi:
    - nuovo_messaggio_wa: messaggio in entrata o in uscita
    - aggiorna_stato_wa: status update (delivered/read/failed)
    - segna_letti_wa: counter non_letti azzerato (sync tra browser aperti)
    """
    async def connect(self):
        from django.contrib.auth.models import AnonymousUser
        if isinstance(self.scope['user'], AnonymousUser):
            await self.close()
            return
        self.room_group_name = 'messaggi_wa'
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await _safe_group_discard(self)

    async def nuovo_messaggio_wa(self, event):
        await self.send(text_data=json.dumps({
            'type': 'nuovo_messaggio_wa',
            'conv_id': event.get('conv_id'),
            'numero_e164': event.get('numero_e164', ''),
            'preview': event.get('preview', ''),
            'direzione': event.get('direzione', 'in'),
            'timestamp': event.get('timestamp', ''),
        }))

    async def aggiorna_stato_wa(self, event):
        await self.send(text_data=json.dumps({
            'type': 'aggiorna_stato_wa',
            'conv_id': event.get('conv_id'),
            'msg_id': event.get('msg_id'),
            'stato': event.get('stato', ''),
        }))

    async def segna_letti_wa(self, event):
        await self.send(text_data=json.dumps({
            'type': 'segna_letti_wa',
            'conv_id': event.get('conv_id'),
        }))