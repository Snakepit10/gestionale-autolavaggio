import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async


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
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    
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
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    
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
            'ordine': event['ordine']
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
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    
    async def stats_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'stats_update',
            'stats': event['stats']
        }))